"""Phase 2: credit expiry, CRM webhooks, SMS/voicemail attribution + transcription, simulator, sender phone."""

import json
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select

from app.core.database import session_factory
from app.models import CreditLedger, LedgerAction
from app.services import credits, crm_webhook, transcription
from tests.test_api_flow import FakeEngine, _create, _run_all

ADMIN = {"X-Admin-Token": "test-admin"}


def _mock_httpx(monkeypatch, handler):
    """Route every httpx.AsyncClient created by app code through a MockTransport."""
    real = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs.pop("transport", None)
        return real(*args, transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)


# --------------------------------------------------------------------------- credits


async def test_credit_expiry_fifo_and_refund(api, queued):
    ws_id = api.workspace_id
    # Initial grant (10, default 60-day expiry) + a later, never-expiring grant of 5.
    r = await api.post(f"/api/v1/admin/workspaces/{ws_id}/credits", json={"amount": 5, "expires_in_days": None}, headers=ADMIN)
    assert r.json()["credits_balance"] == 15

    camp = await _create(api, domains=["a1.com", "a2.com", "a3.com"])
    await api.post(f"/api/v1/campaigns/{camp['id']}/launch", json={})
    from app.models.enums import SubmissionStatus

    await _run_all(queued["submissions"], {"a3.com": (SubmissionStatus.FAILED_VALIDATION, True)})  # 2 kept, 1 refunded

    ledger = (await api.get("/api/v1/workspace/ledger")).json()["items"]
    grants = sorted([i for i in ledger if i["action"] == "grant"], key=lambda g: g["expires_at"] is None)
    assert grants[0]["expires_at"] is not None and grants[0]["remaining"] == 8  # FIFO: soonest-expiring grant used
    assert grants[1]["expires_at"] is None and grants[1]["remaining"] == 5

    # Nothing is due yet
    assert (await api.post("/api/v1/admin/expire-credits", headers=ADMIN)).json() == {"expired_grants": 0, "credits_expired": 0}

    # Jump 61 days ahead: the 8 unused credits of the first grant expire; the never-expiring 5 stay.
    async with session_factory()() as s:
        res = await credits.expire_due_credits(s, now=datetime.now(timezone.utc) + timedelta(days=61))
    assert (res.expired_grants, res.credits_expired) == (1, 8)
    ws = (await api.get("/api/v1/workspace")).json()
    assert ws["credits_balance"] == 5
    expired = [i for i in (await api.get("/api/v1/workspace/ledger")).json()["items"] if i["action"] == "expired"]
    assert len(expired) == 1 and expired[0]["amount"] == -8

    # Idempotent
    async with session_factory()() as s:
        res = await credits.expire_due_credits(s, now=datetime.now(timezone.utc) + timedelta(days=62))
    assert res.expired_grants == 0


async def test_expiry_never_makes_balance_negative(api, queued):
    async with session_factory()() as s:
        g = (await s.scalars(select(CreditLedger).where(CreditLedger.action == LedgerAction.grant))).first()
        g.remaining = 50  # inconsistent on purpose: more "remaining" than the balance holds
        await s.commit()
        res = await credits.expire_due_credits(s, now=datetime.now(timezone.utc) + timedelta(days=61))
    assert res.credits_expired == 10
    assert (await api.get("/api/v1/workspace")).json()["credits_balance"] == 0


# --------------------------------------------------------------------------- CRM webhook


async def test_positive_reply_dispatches_signed_webhook(api, queued, monkeypatch):
    sent = []

    def handler(request: httpx.Request):
        sent.append(request)
        return httpx.Response(200, json={"ok": True})

    r = await api.patch("/api/v1/workspace", json={"webhook_url": "https://crm.example.com/hooks/raven"})
    secret = r.json()["webhook_secret"]
    assert secret.startswith("whsec_")

    camp = await _create(api, domains=["newportsg.com"])
    await api.post(f"/api/v1/campaigns/{camp['id']}/launch", json={})
    await _run_all(queued["submissions"], {})
    sub = (await api.get("/api/v1/submissions")).json()["items"][0]

    _mock_httpx(monkeypatch, handler)
    msg = {"From": "jane@newportsg.com", "To": sub["unique_reply_email"], "Subject": "Re", "TextBody": "Yes, when can we talk?", "MessageID": "p2-1", "Headers": []}
    await api.post("/api/v1/webhooks/inbound/postmark?token=test-hook", json=msg)

    assert len(sent) == 1
    req = sent[0]
    body = req.content
    ts = int(req.headers["X-Raven-Timestamp"])
    assert req.headers["X-Raven-Signature"] == crm_webhook.sign(secret, ts, body)
    payload = json.loads(body)
    assert payload["event"] == "reply.positive"
    assert payload["company"]["domain"] == "newportsg.com"
    assert payload["campaign"]["name"] == camp["name"] and payload["variant"]["label"] in ("A", "B")

    # Manual reclassification of an already-dispatched reply doesn't send it twice
    reply = (await api.get("/api/v1/replies")).json()["items"][0]
    await api.patch(f"/api/v1/replies/{reply['id']}", json={"classification": "not_sure"})
    await api.patch(f"/api/v1/replies/{reply['id']}", json={"classification": "positive"})
    assert len(sent) == 1


async def test_webhook_test_endpoint_logs_without_url(api):
    r = (await api.post("/api/v1/workspace/webhook/test")).json()
    assert r["logged_only"] is True and r["delivered"] is False
    assert r["payload"]["event"] == "reply.positive"
    assert (await api.patch("/api/v1/workspace", json={"webhook_url": "ftp://nope"})).status_code == 422


async def test_webhook_failure_is_reported(api, monkeypatch):
    await api.patch("/api/v1/workspace", json={"webhook_url": "https://crm.example.com/down"})
    _mock_httpx(monkeypatch, lambda req: httpx.Response(503))
    r = (await api.post("/api/v1/workspace/webhook/test")).json()
    assert r["delivered"] is False and r["error"] == "HTTP 503"
    old = (await api.get("/api/v1/workspace")).json()["webhook_secret"]
    new = (await api.post("/api/v1/workspace/webhook/rotate-secret")).json()["webhook_secret"]
    assert new != old


# --------------------------------------------------------------------------- phone attribution


async def _phone_campaign(api, queued):
    await api.patch("/api/v1/workspace", json={"default_sender_phone": "+1 555 000 2222"})
    camp = await _create(api, name="Phones")
    csv_bytes = b"domain,company_name,phone\nnewportsg.com,Newport Search Group,(555) 123-4567\nrivalstaffing.com,Rival Staffing,\n"
    await api.post(f"/api/v1/campaigns/{camp['id']}/targets/csv", files={"file": ("t.csv", csv_bytes, "text/csv")})
    await api.post(f"/api/v1/campaigns/{camp['id']}/launch", json={})
    engine, _ = await _run_all(queued["submissions"], {})
    return camp, engine


async def test_default_sender_phone_used_on_forms(api, queued):
    _, engine = await _phone_campaign(api, queued)
    assert all(sender.phone == "+15550002222" for _, sender in engine.seen)


async def test_sms_attribution_by_company_phone_and_previous_reply(api, queued):
    camp, _ = await _phone_campaign(api, queued)
    # Twilio SMS to the workspace default sender number, from the company's listed phone
    params = {"From": "+15551234567", "To": "+15550002222", "Body": "Yes, call me tomorrow", "MessageSid": "SMa"}
    import base64
    import hashlib
    import hmac

    def sig(p):
        url = "https://api.test.dev/api/v1/webhooks/twilio/sms"
        return base64.b64encode(hmac.new(b"twilio-secret", (url + "".join(f"{k}{p[k]}" for k in sorted(p))).encode(), hashlib.sha1).digest()).decode()

    r = await api.post("/api/v1/webhooks/twilio/sms", data=params, headers={"X-Twilio-Signature": sig(params)})
    assert r.status_code == 200
    reply = (await api.get("/api/v1/replies")).json()["items"][0]
    assert reply["domain"] == "newportsg.com" and reply["campaign_id"] == camp["id"] and reply["variant_label"] in ("A", "B")
    assert reply["classification"] == "positive"


async def test_voicemail_attribution_by_mention_and_learns_phone(api, queued):
    camp, _ = await _phone_campaign(api, queued)
    r = await api.post(
        "/api/v1/simulate/inbound",
        json={"channel": "voicemail", "from_number": "+1 (555) 999-0000", "body": "Hi, this is Mark from Rival Staffing. Not interested, please remove us."},
    )
    assert r.status_code == 200, r.text
    vm = r.json()
    assert vm["domain"] == "rivalstaffing.com" and vm["campaign_name"] == camp["name"]
    assert vm["classification"] == "negative" and vm["channel"] == "voicemail"
    assert [d["pattern"] for d in (await api.get("/api/v1/do-not-contact")).json()["items"]] == ["rivalstaffing.com"]

    # The number was learned: a follow-up SMS with no company name still maps to Rival Staffing
    r = await api.post("/api/v1/simulate/inbound", json={"channel": "sms", "from_number": "+15559990000", "body": "STOP"})
    assert r.json()["domain"] == "rivalstaffing.com"

    # Unknown number, no clues -> stored for the workspace, unmatched
    r = await api.post("/api/v1/simulate/inbound", json={"channel": "sms", "from_number": "+15550000001", "body": "Who is this?"})
    assert r.json()["company_id"] is None and r.json()["classification"] == "not_sure"


async def test_simulator_email_and_validation(api, queued):
    await _create(api, domains=["acme-staffing.com"])
    camps = (await api.get("/api/v1/campaigns")).json()["items"]
    await api.post(f"/api/v1/campaigns/{camps[0]['id']}/launch", json={})
    await _run_all(queued["submissions"], {})
    r = await api.post("/api/v1/simulate/inbound", json={"channel": "email", "from_email": "ceo@acme-staffing.com", "company_domain": "acme-staffing.com", "body": "Sounds good, send me your rates"})
    assert r.status_code == 200 and r.json()["classification"] == "positive" and r.json()["domain"] == "acme-staffing.com"
    assert (await api.post("/api/v1/simulate/inbound", json={"channel": "sms", "body": "hi"})).status_code == 422


# --------------------------------------------------------------------------- voicemail transcription


async def test_twilio_recording_without_provider_uses_placeholder(api, queued, monkeypatch):
    monkeypatch.setattr(transcription, "provider", lambda: None)
    await api.patch("/api/v1/workspace", json={"default_sender_phone": "+15550002222"})
    params = {"CallSid": "CA1", "RecordingSid": "RE1", "RecordingUrl": "https://api.twilio.com/rec/RE1", "RecordingStatus": "completed"}
    url_q = "?from=%2B15557770000&to=%2B15550002222"
    import base64
    import hashlib
    import hmac

    full = "https://api.test.dev/api/v1/webhooks/twilio/recording" + url_q
    sig = base64.b64encode(hmac.new(b"twilio-secret", (full + "".join(f"{k}{params[k]}" for k in sorted(params))).encode(), hashlib.sha1).digest()).decode()
    r = await api.post("/api/v1/webhooks/twilio/recording" + url_q, data=params, headers={"X-Twilio-Signature": sig})
    assert r.status_code == 200, r.text
    vm = (await api.get("/api/v1/replies")).json()["items"][0]
    assert vm["channel"] == "voicemail" and vm["body"] == transcription.PLACEHOLDER
    assert vm["recording_url"] == "https://api.twilio.com/rec/RE1" and vm["from_address"] == "+15557770000"


async def test_deepgram_transcription(monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "deepgram_api_key", "dg-test")
    calls = []

    def handler(request: httpx.Request):
        calls.append(str(request.url))
        if "twilio" in request.url.host:
            return httpx.Response(200, content=b"ID3fake", headers={"content-type": "audio/mpeg"})
        assert request.headers["Authorization"] == "Token dg-test"
        return httpx.Response(200, json={"results": {"channels": [{"alternatives": [{"transcript": "Yes please call me back"}]}]}})

    _mock_httpx(monkeypatch, handler)
    assert await transcription.transcribe("https://api.twilio.com/rec/RE9") == "Yes please call me back"
    assert calls[0].endswith("RE9.mp3") and "deepgram" in calls[1]


async def test_whisper_failure_returns_none(monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "deepgram_api_key", None)
    monkeypatch.setattr(get_settings(), "openai_api_key", "sk-test")
    _mock_httpx(monkeypatch, lambda req: httpx.Response(500))
    assert await transcription.transcribe("https://example.com/vm.mp3") is None


async def test_attribution_matches_domain_stem_spoken_as_words(api, queued):
    camp = await _create(api, name="Stem", domains=["spencerstuart.com"])
    await api.post(f"/api/v1/campaigns/{camp['id']}/launch", json={})
    await _run_all(queued["submissions"], {})
    r = await api.post("/api/v1/simulate/inbound", json={"channel": "voicemail", "from_number": "+15554440000", "body": "This is Tom from Spencer Stuart, call me back"})
    assert r.json()["domain"] == "spencerstuart.com"
