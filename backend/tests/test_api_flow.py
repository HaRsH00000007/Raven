"""API + worker flow tests on SQLite with a fake form engine (no browser, no Redis)."""

import base64
import hashlib
import hmac
import uuid

import pytest

from app.models.enums import SubmissionStatus
from app.workers import submission_worker
from app.workers.form_probe import ProbeResult

VARIANTS = [
    {"label": "A", "sender_name": "Rohit Singh", "message_body": "Are you hiring recruiters this quarter, {{company_name}}?"},
    {"label": "B", "sender_name": "Rohit Singh", "message_body": "Do you work with offshore recruiters?"},
]


class FakeEngine:
    """Stands in for FormEngine. `plan` maps domain -> (status, clicks_submit)."""

    def __init__(self, plan):
        self.plan = plan
        self.seen = []

    async def probe(self, domain, sender, opts, robots, before_submit=None, field_mapper=None):
        self.seen.append((domain, sender))
        status, clicks = self.plan.get(domain, (SubmissionStatus.SUCCESS_VERIFIED, True))
        if clicks and before_submit and not await before_submit():
            from app.workers.form_probe import NoCreditsError

            raise NoCreditsError()
        return ProbeResult(
            status=status,
            detail="fake",
            contact_url=f"https://{domain}/contact",
            fields_filled={"email": sender.email, "message": sender.message},
            pre_screenshot=f"{opts.run_id}_pre.png",
            post_screenshot=f"{opts.run_id}_post.png" if clicks else None,
            submit_clicked=clicks,
        )


async def _run_all(ids, plan):
    engine = FakeEngine(plan)
    ctx = {"engine": engine, "robots": object()}
    results = [await submission_worker.process_submission(ctx, str(i)) for i in ids]
    return engine, results


async def _create(api, name="Nov", domains=None):
    r = await api.post(
        "/api/v1/campaigns",
        json={"name": name, "sender_company": "Glocal RPO", "variants": VARIANTS, "domains": domains or []},
    )
    assert r.status_code == 201, r.text
    return r.json()


async def test_campaign_lifecycle(api, queued):
    camp = await _create(api, domains=["https://www.Newportsg.com/about", "incipioworks.com", "incipioworks.com", "not a domain", "acme.io"])
    assert camp["targets"] == 3
    assert [v["label"] for v in camp["variants"]] == ["A", "B"]

    r = await api.post(f"/api/v1/campaigns/{camp['id']}/launch", json={"dry_run": False})
    assert r.status_code == 200, r.text
    assert r.json()["queued"] == 3
    ids = queued["submissions"]

    engine, results = await _run_all(
        ids,
        {
            "newportsg.com": (SubmissionStatus.SUCCESS_VERIFIED, True),
            "incipioworks.com": (SubmissionStatus.FAILED_VALIDATION, True),  # charged then refunded
            "acme.io": (SubmissionStatus.SKIPPED_CAPTCHA, False),  # never charged
        },
    )
    assert sorted(results) == sorted(["SUCCESS_VERIFIED", "FAILED_VALIDATION", "SKIPPED_CAPTCHA"])
    # Personalisation + unique reply address per submission
    for domain, sender in engine.seen:
        assert sender.email.startswith("r-") and sender.email.endswith("@replies.test.dev")
    assert any("newportsg.com?" in s.message for _, s in engine.seen) or any("Do you work" in s.message for _, s in engine.seen)

    ws = (await api.get("/api/v1/workspace")).json()
    assert ws["credits_balance"] == 9  # 10 - 1 sent (validation failure refunded)
    ledger = (await api.get("/api/v1/workspace/ledger")).json()
    assert sorted(i["action"] for i in ledger["items"]) == ["grant", "refund_skip", "used", "used"]

    detail = (await api.get(f"/api/v1/campaigns/{camp['id']}")).json()
    assert detail["status"] == "completed"
    assert detail["sent"] == 1 and detail["pending"] == 0
    assert sum(v["sent"] for v in detail["variants"]) == 1

    subs = (await api.get("/api/v1/submissions", params={"status": "SUCCESS_VERIFIED"})).json()
    assert subs["total"] == 1 and subs["items"][0]["domain"] == "newportsg.com"
    sid = subs["items"][0]["id"]
    # Screenshot file doesn't exist on disk in this test -> clean 404, not a 500
    assert (await api.get(f"/api/v1/submissions/{sid}/screenshots/pre")).status_code == 404


async def test_reply_triage_dnc_and_cooldown(api, queued):
    camp = await _create(api, domains=["newportsg.com", "rivalstaffing.com"])
    await api.post(f"/api/v1/campaigns/{camp['id']}/launch", json={})
    await _run_all(queued["submissions"], {})
    subs = {s["domain"]: s for s in (await api.get("/api/v1/submissions")).json()["items"]}

    # Positive email reply via Postmark, addressed to the per-submission alias
    pos = {
        "From": "Jane Roe <jane@newportsg.com>",
        "To": subs["newportsg.com"]["unique_reply_email"],
        "OriginalRecipient": subs["newportsg.com"]["unique_reply_email"],
        "Subject": "Re: RPO",
        "TextBody": "We are actively meeting with prospective RPO partners. When can we talk?\n\nOn Tue, Rohit wrote:\n> Are you hiring",
        "MessageID": "msg-1",
        "Headers": [],
    }
    r = await api.post("/api/v1/webhooks/inbound/postmark?token=test-hook", json=pos)
    assert r.json()["status"] == "stored", r.text
    # Retried webhook is idempotent
    r2 = await api.post("/api/v1/webhooks/inbound/postmark?token=test-hook", json=pos)
    assert r2.json()["reply_id"] == r.json()["reply_id"]

    neg = {
        "from": "ops@rivalstaffing.com",
        "to": f"Rohit <{subs['rivalstaffing.com']['unique_reply_email']}>",
        "subject": "Re: hello",
        "text": "Not interested, thanks. Please remove us.",
        "headers": "Message-ID: <neg-1@rival>\nAuto-Submitted: no",
        "envelope": "{}",
    }
    r = await api.post("/api/v1/webhooks/inbound/sendgrid?token=test-hook", data=neg)
    assert r.json()["status"] == "stored", r.text

    auto = dict(pos, MessageID="msg-2", TextBody="Thank you for contacting us. A member of our team will be in touch.", Subject="We've received your message", Headers=[{"Name": "Auto-Submitted", "Value": "auto-replied"}])
    await api.post("/api/v1/webhooks/inbound/postmark?token=test-hook", json=auto)

    # Bad token rejected; unknown recipient ignored
    assert (await api.post("/api/v1/webhooks/inbound/postmark?token=nope", json=pos)).status_code == 401
    stray = dict(pos, To="hello@replies.test.dev", OriginalRecipient="", MessageID="msg-3")
    assert (await api.post("/api/v1/webhooks/inbound/postmark?token=test-hook", json=stray)).json()["status"] == "ignored"

    replies = (await api.get("/api/v1/replies")).json()
    assert replies["counts"] == {"positive": 1, "not_sure": 0, "negative": 1, "automatic": 1}
    positive = (await api.get("/api/v1/replies", params={"classification": "positive"})).json()["items"][0]
    assert positive["domain"] == "newportsg.com" and positive["variant_label"] in ("A", "B")
    assert "wrote:" not in positive["body"]  # quoted history stripped

    dnc = (await api.get("/api/v1/do-not-contact")).json()["items"]
    assert [d["pattern"] for d in dnc] == ["rivalstaffing.com"]

    # Mark call booked, then check stats
    await api.patch(f"/api/v1/replies/{positive['id']}", json={"call_booked": True})
    stats = (await api.get("/api/v1/stats/overview")).json()
    assert stats["forms_sent"] == 2
    assert stats["replies"] == 2 and stats["positive"] == 1 and stats["calls_booked"] == 1
    assert stats["spend_cents"] == 12 and stats["cost_per_positive_cents"] == 12
    assert [f["stage"] for f in stats["funnel"]] == ["Targets", "Forms sent", "Replies", "Positive", "Calls booked"]

    # A second campaign to the same firms: DNC'd firm skipped, the other is in cooldown.
    camp2 = await _create(api, name="Dec", domains=["newportsg.com", "rivalstaffing.com"])
    queued["submissions"].clear()
    await api.post(f"/api/v1/campaigns/{camp2['id']}/launch", json={})
    engine, results = await _run_all(queued["submissions"], {})
    assert sorted(results) == ["SKIPPED_COOLDOWN", "SKIPPED_DNC"]
    assert engine.seen == []  # the browser was never opened


async def test_out_of_credits_pauses_campaign(api, queued):
    # Spend the balance down to zero first
    r = await api.post(
        f"/api/v1/admin/workspaces/{api.workspace_id}/credits",
        json={"amount": -10},
        headers={"X-Admin-Token": "test-admin"},
    )
    assert r.json()["credits_balance"] == 0
    camp = await _create(api, domains=["a-firm.com"])
    assert (await api.post(f"/api/v1/campaigns/{camp['id']}/launch", json={})).status_code == 402
    # Dry runs need no credits and never charge
    r = await api.post(f"/api/v1/campaigns/{camp['id']}/launch", json={"dry_run": True})
    assert r.status_code == 200
    _, results = await _run_all(queued["submissions"], {"a-firm.com": (SubmissionStatus.DRY_RUN, False)})
    assert results == ["DRY_RUN"]

    camp2 = await _create(api, name="real", domains=["b-firm.com"])
    await api.post(f"/api/v1/admin/workspaces/{api.workspace_id}/credits", json={"amount": 1}, headers={"X-Admin-Token": "test-admin"})
    await api.post(f"/api/v1/campaigns/{camp2['id']}/launch", json={})
    await api.post(f"/api/v1/admin/workspaces/{api.workspace_id}/credits", json={"amount": -1}, headers={"X-Admin-Token": "test-admin"})
    _, results = await _run_all([queued["submissions"][-1]], {})
    assert results == ["no-credits"]
    assert (await api.get(f"/api/v1/campaigns/{camp2['id']}")).json()["status"] == "paused"


async def test_variant_replace_and_validation(api, queued):
    camp = await _create(api, domains=["x-firm.com"])
    bad = await api.put(f"/api/v1/campaigns/{camp['id']}/variants", json={"variants": [VARIANTS[0], VARIANTS[0]]})
    assert bad.status_code == 422
    r = await api.put(
        f"/api/v1/campaigns/{camp['id']}/variants",
        json={"variants": [{"label": "C", "sender_name": "Tom", "message_body": "One short question?"}]},
    )
    assert [v["label"] for v in r.json()["variants"]] == ["C"]
    csv_bytes = b"company_name,website\nYork Staffing,https://yorkstaffing.com\nBad,???\n"
    r = await api.post(f"/api/v1/campaigns/{camp['id']}/targets/csv", files={"file": ("t.csv", csv_bytes, "text/csv")})
    assert r.json() == {"added": 1, "duplicates": 0, "invalid": ["???"]}


async def test_auth_required(client):
    assert (await client.get("/api/v1/workspace")).status_code == 401
    assert (await client.get("/api/v1/workspace", headers={"X-API-Key": "rvn_wrong"})).status_code == 401


async def test_twilio_sms_signature(api, queued):
    await api.patch("/api/v1/workspace", json={"reply_phone_number": "+1 (555) 000-1111"})
    params = {"From": "+15552223333", "To": "+15550001111", "Body": "Yes, call me tomorrow", "MessageSid": "SM1"}
    url = "https://api.test.dev/api/v1/webhooks/twilio/sms"
    payload = url + "".join(f"{k}{params[k]}" for k in sorted(params))
    sig = base64.b64encode(hmac.new(b"twilio-secret", payload.encode(), hashlib.sha1).digest()).decode()
    assert (await api.post("/api/v1/webhooks/twilio/sms", data=params)).status_code == 403
    r = await api.post("/api/v1/webhooks/twilio/sms", data=params, headers={"X-Twilio-Signature": sig})
    assert r.status_code == 200 and "<Response/>" in r.text
    replies = (await api.get("/api/v1/replies", params={"classification": "positive"})).json()["items"]
    assert replies[0]["channel"] == "sms" and replies[0]["from_address"] == "+15552223333"
