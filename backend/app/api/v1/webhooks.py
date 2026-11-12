import json
from urllib.parse import urlencode
from xml.sax.saxutils import escape

from fastapi import APIRouter, HTTPException, Query, Request, Response

from app.api.deps import DB
from app.core.config import get_settings
from app.models import ReplyChannel
from app.services.email_utils import parse_raw_headers
from app.services.inbound import (
    InboundEmail,
    ingest_email,
    ingest_phone,
    twilio_signature_valid,
    update_voicemail_transcript,
)
from app.services.security import constant_time_equals

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

TWIML = "application/xml"
EMPTY_TWIML = '<?xml version="1.0" encoding="UTF-8"?><Response/>'


def _check_token(token: str | None) -> None:
    if not token or not constant_time_equals(token, get_settings().inbound_webhook_token):
        raise HTTPException(401, "Invalid webhook token")


@router.post("/inbound/postmark")
async def postmark_inbound(request: Request, db: DB, token: str | None = Query(None)):
    _check_token(token)
    try:
        p = await request.json()
    except json.JSONDecodeError:
        raise HTTPException(400, "Invalid JSON")
    recipients = [p.get("OriginalRecipient") or "", p.get("To") or "", p.get("Cc") or "", p.get("Bcc") or ""]
    for key in ("ToFull", "CcFull", "BccFull"):
        recipients += [x.get("Email", "") for x in (p.get(key) or []) if isinstance(x, dict)]
    headers = {h.get("Name", ""): h.get("Value", "") for h in (p.get("Headers") or []) if isinstance(h, dict)}
    reply = await ingest_email(
        db,
        InboundEmail(
            recipients=recipients,
            from_raw=p.get("From"),
            subject=p.get("Subject"),
            text=p.get("TextBody"),
            html=p.get("HtmlBody"),
            stripped_reply=p.get("StrippedTextReply"),
            headers=headers,
            message_id=p.get("MessageID"),
        ),
    )
    # Always 200 for unmatched mail so the provider doesn't retry forever.
    return {"status": "stored" if reply else "ignored", "reply_id": str(reply.id) if reply else None}


@router.post("/inbound/sendgrid")
async def sendgrid_inbound(request: Request, db: DB, token: str | None = Query(None)):
    _check_token(token)
    form = await request.form()
    get = lambda k: (str(form.get(k)) if form.get(k) is not None else None)  # noqa: E731
    recipients = [get("to") or "", get("cc") or ""]
    try:
        env = json.loads(get("envelope") or "{}")
        recipients += env.get("to") or []
    except json.JSONDecodeError:
        pass
    headers = parse_raw_headers(get("headers") or "")
    reply = await ingest_email(
        db,
        InboundEmail(
            recipients=recipients,
            from_raw=get("from"),
            subject=get("subject"),
            text=get("text"),
            html=get("html"),
            headers=headers,
            message_id=headers.get("Message-ID") or headers.get("Message-Id"),
        ),
    )
    return {"status": "stored" if reply else "ignored", "reply_id": str(reply.id) if reply else None}


async def _twilio_params(request: Request) -> dict[str, str]:
    form = await request.form()
    params = {k: str(v) for k, v in form.items()}
    settings = get_settings()
    if settings.twilio_auth_token:
        url = settings.public_base_url.rstrip("/") + request.url.path
        if request.url.query:
            url += "?" + request.url.query
        if not twilio_signature_valid(settings.twilio_auth_token, url, params, request.headers.get("X-Twilio-Signature")):
            raise HTTPException(403, "Invalid Twilio signature")
    elif settings.environment == "production":
        raise HTTPException(503, "TWILIO_AUTH_TOKEN must be set in production")
    return params


@router.post("/twilio/sms")
async def twilio_sms(request: Request, db: DB):
    p = await _twilio_params(request)
    await ingest_phone(
        db,
        channel=ReplyChannel.sms,
        to_number=p.get("To", ""),
        from_number=p.get("From", ""),
        body=p.get("Body", ""),
        provider_id=p.get("MessageSid") or p.get("SmsSid") or "",
    )
    return Response(EMPTY_TWIML, media_type=TWIML)


@router.post("/twilio/voice")
async def twilio_voice(request: Request):
    """Answers the Raven number: plays a greeting and records a voicemail. When the recording is ready Twilio
    calls /twilio/recording (we carry From/To in its query string, since that callback doesn't include them)."""
    p = await _twilio_params(request)
    base = get_settings().public_base_url.rstrip("/") + "/api/v1/webhooks/twilio/recording"
    callback = escape(base + "?" + urlencode({"from": p.get("From", ""), "to": p.get("To", "")}))
    twiml = (
        '<?xml version="1.0" encoding="UTF-8"?><Response>'
        "<Say>Thanks for calling back. Please leave your name, company and a short message after the tone, "
        "and we will return your call.</Say>"
        f'<Record maxLength="180" playBeep="true" recordingStatusCallback="{callback}" '
        'recordingStatusCallbackEvent="completed"/>'
        "<Say>We did not receive a message. Goodbye.</Say></Response>"
    )
    return Response(twiml, media_type=TWIML)


@router.post("/twilio/recording")
async def twilio_recording(request: Request, db: DB):
    """Recording status callback. The worker transcribes the recording (Deepgram/Whisper if configured).
    A `TranscriptionText` form field, if present, is used directly (handy for local simulation)."""
    p = await _twilio_params(request)
    if p.get("RecordingStatus", "completed") != "completed":
        return {"status": "ignored"}
    q = request.query_params
    await ingest_phone(
        db,
        channel=ReplyChannel.voicemail,
        to_number=q.get("to") or p.get("To", ""),
        from_number=q.get("from") or p.get("From", ""),
        body=p.get("TranscriptionText", ""),
        provider_id=p.get("CallSid") or p.get("RecordingSid") or "",
        recording_url=p.get("RecordingUrl"),
    )
    return {"status": "ok"}


@router.post("/twilio/transcription")
async def twilio_transcription(request: Request, db: DB):
    """Twilio's optional built-in transcription callback: fills in (or creates) the voicemail reply."""
    p = await _twilio_params(request)
    text = p.get("TranscriptionText") or ""
    if p.get("TranscriptionStatus", "completed") != "completed":
        text = ""
    call_sid = p.get("CallSid") or ""
    existing = await update_voicemail_transcript(db, call_sid, text) if call_sid else None
    if existing is None:
        await ingest_phone(
            db,
            channel=ReplyChannel.voicemail,
            to_number=p.get("To", ""),
            from_number=p.get("From", ""),
            body=text,
            provider_id=call_sid or p.get("RecordingSid") or "",
            recording_url=p.get("RecordingUrl"),
        )
    return {"status": "ok"}
