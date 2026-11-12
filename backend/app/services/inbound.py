"""Turn provider webhooks (or the local simulator) into InboundReply rows, attribute them, and triage."""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import Campaign, Company, InboundReply, ReplyChannel, Submission, SubmissionStatus, Workspace
from app.services.email_utils import extract_submission_id, headers_indicate_auto, html_to_text, parse_address, strip_quoted
from app.services.form_rules import normalize_domain
from app.workers import queue
from app.workers.triage_worker import process_reply

log = logging.getLogger("raven.inbound")

ATTRIBUTION_WINDOW_DAYS = 120
SENT_LIKE = (SubmissionStatus.SUCCESS_VERIFIED, SubmissionStatus.SUBMITTED_UNVERIFIED, SubmissionStatus.DRY_RUN)


@dataclass
class InboundEmail:
    recipients: list[str]
    from_raw: str | None
    subject: str | None
    text: str | None
    html: str | None
    stripped_reply: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    message_id: str | None = None


# ---------------------------------------------------------------------------
# Phone helpers
# ---------------------------------------------------------------------------


def phone_digits(raw: str | None) -> str | None:
    """'+1 (555) 000-1111' -> '15550001111'. None if too short to be a phone number."""
    if not raw:
        return None
    d = re.sub(r"\D", "", raw)
    return d if len(d) >= 7 else None


def e164(raw: str | None) -> str | None:
    d = phone_digits(raw)
    return f"+{d}" if d else None


def _tail(d: str) -> str:
    # Compare on the last 10 digits so "+1 555..." and "555..." match.
    return d[-10:]


async def workspace_for_number(session: AsyncSession, to_number: str) -> Workspace | None:
    num = e164(to_number)
    if not num:
        return None
    ws = await session.scalar(
        select(Workspace).where(or_(Workspace.reply_phone_number == num, Workspace.default_sender_phone == num)).limit(1)
    )
    if ws:
        return ws
    # A campaign-level sender phone also routes back to its workspace.
    return await session.scalar(
        select(Workspace).join(Campaign, Campaign.workspace_id == Workspace.id).where(Campaign.sender_phone == num).limit(1)
    )


async def _latest_submission(session: AsyncSession, workspace_id: uuid.UUID, company_id: uuid.UUID) -> Submission | None:
    return await session.scalar(
        select(Submission)
        .where(Submission.workspace_id == workspace_id, Submission.company_id == company_id, Submission.status.in_(SENT_LIKE))
        .order_by(Submission.submitted_at.desc().nulls_last(), Submission.created_at.desc())
        .limit(1)
    )


async def attribute_phone_reply(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    from_number: str,
    body: str,
    *,
    hint_domain: str | None = None,
    hint_submission_id: uuid.UUID | None = None,
) -> tuple[uuid.UUID | None, uuid.UUID | None, str]:
    """Returns (company_id, submission_id, how). `how` explains the match for logs/tests."""
    if hint_submission_id:
        sub = await session.get(Submission, hint_submission_id)
        if sub and sub.workspace_id == workspace_id:
            return sub.company_id, sub.id, "submission_hint"

    company: Company | None = None
    how = "unmatched"
    digits = phone_digits(from_number)

    if hint_domain and (d := normalize_domain(hint_domain)):
        company = await session.scalar(select(Company).where(Company.workspace_id == workspace_id, Company.domain == d))
        how = "domain_hint" if company else how

    if company is None and digits:
        company = await session.scalar(
            select(Company)
            .where(Company.workspace_id == workspace_id, Company.phone.is_not(None), Company.phone.like(f"%{_tail(digits)}"))
            .limit(1)
        )
        how = "company_phone" if company else how

    if company is None and digits:
        prev = await session.scalar(
            select(InboundReply)
            .where(
                InboundReply.workspace_id == workspace_id,
                InboundReply.from_address == e164(from_number),
                InboundReply.company_id.is_not(None),
            )
            .order_by(InboundReply.created_at.desc())
            .limit(1)
        )
        if prev:
            if prev.submission_id:
                return prev.company_id, prev.submission_id, "previous_reply"
            company = await session.get(Company, prev.company_id)
            how = "previous_reply"

    if company is None and body:
        # Message mentions a company we recently contacted ("Hi, this is Jane from Acme Staffing").
        since = datetime.now(timezone.utc) - timedelta(days=ATTRIBUTION_WINDOW_DAYS)
        text = body.lower()
        rows = await session.execute(
            select(Company)
            .join(Submission, Submission.company_id == Company.id)
            .where(Submission.workspace_id == workspace_id, Submission.status.in_(SENT_LIKE), Submission.created_at >= since)
            .distinct()
            .limit(20000)
        )
        squashed = re.sub(r"[^a-z0-9]", "", text)  # "Spencer Stuart" -> "spencerstuart"
        best: tuple[int, Company] | None = None
        for (c,) in rows.all():
            base = c.domain.split(".")[0]
            for needle in (c.domain, base if len(base) >= 5 else None, c.company_name):
                if needle and len(needle) >= 4 and re.search(rf"(?<![a-z0-9]){re.escape(needle.lower())}(?![a-z0-9])", text):
                    if best is None or len(needle) > best[0]:
                        best = (len(needle), c)
            # Spoken / typed names often split the domain stem into words; only for distinctive stems.
            if best is None and len(base) >= 8 and base.replace("-", "") in squashed:
                best = (len(base), c)
        if best:
            company = best[1]
            how = "mentioned_in_message"

    if company is None:
        return None, None, "unmatched"
    if digits and not company.phone:
        company.phone = digits  # learn the number for next time
    sub = await _latest_submission(session, workspace_id, company.id)
    return company.id, sub.id if sub else None, how


# ---------------------------------------------------------------------------
# Persistence + triage
# ---------------------------------------------------------------------------


async def _save_and_triage(session: AsyncSession, reply: InboundReply, inline: bool = False) -> InboundReply:
    session.add(reply)
    try:
        await session.commit()
    except IntegrityError:
        # Provider retried a webhook we already stored.
        await session.rollback()
        existing = await session.scalar(
            select(InboundReply).where(InboundReply.provider_message_id == reply.provider_message_id)
        )
        if existing is None:
            raise
        return existing
    if inline:
        await process_reply(session, reply.id)
        return reply
    try:
        await queue.enqueue_reply(reply.id)
    except Exception as e:  # Redis down: triage inline so the reply is never left unclassified
        log.warning("Queue unavailable (%s); triaging reply %s inline", e, reply.id)
        await process_reply(session, reply.id)
    return reply


async def ingest_email(session: AsyncSession, msg: InboundEmail, inline: bool = False) -> InboundReply | None:
    settings = get_settings()
    sid = extract_submission_id(msg.recipients, settings.reply_domain)
    if sid is None:
        log.info("Inbound email with no reply address; recipients=%s", msg.recipients[:5])
        return None
    sub = await session.get(Submission, sid)
    if sub is None:
        log.info("Inbound email for unknown submission %s", sid)
        return None
    from_name, from_addr = parse_address(msg.from_raw)
    raw_text = msg.text or html_to_text(msg.html or "")
    body = (msg.stripped_reply or "").strip() or strip_quoted(raw_text)
    message_id = msg.message_id or msg.headers.get("Message-ID") or msg.headers.get("Message-Id")
    reply = InboundReply(
        workspace_id=sub.workspace_id,
        submission_id=sub.id,
        company_id=sub.company_id,
        channel=ReplyChannel.email,
        provider_message_id=(message_id or f"email:{uuid.uuid4()}")[:255],
        from_address=from_addr,
        from_name=from_name,
        subject=(msg.subject or "")[:998] or None,
        body=body[:20000],
        raw_body=raw_text[:100000],
        is_auto_submitted=headers_indicate_auto(msg.headers),
    )
    return await _save_and_triage(session, reply, inline)


async def ingest_phone(
    session: AsyncSession,
    *,
    channel: ReplyChannel,
    from_number: str,
    body: str,
    provider_id: str,
    to_number: str | None = None,
    workspace: Workspace | None = None,
    recording_url: str | None = None,
    hint_domain: str | None = None,
    hint_submission_id: uuid.UUID | None = None,
    inline: bool = False,
) -> InboundReply | None:
    """SMS or voicemail. For voicemail, pass body="" to have the triage worker transcribe `recording_url`."""
    ws = workspace or (await workspace_for_number(session, to_number or ""))
    if ws is None:
        log.info("Inbound %s to unknown number %s", channel.value, to_number)
        return None
    company_id, submission_id, how = await attribute_phone_reply(
        session, ws.id, from_number, body, hint_domain=hint_domain, hint_submission_id=hint_submission_id
    )
    log.info("Inbound %s from %s attributed via %s", channel.value, from_number, how)
    reply = InboundReply(
        workspace_id=ws.id,
        company_id=company_id,
        submission_id=submission_id,
        channel=channel,
        provider_message_id=(provider_id or f"{channel.value}:{uuid.uuid4()}")[:255],
        from_address=e164(from_number) or from_number,
        body=body[:20000],
        raw_body=body[:100000],
        recording_url=recording_url,
    )
    return await _save_and_triage(session, reply, inline)


async def update_voicemail_transcript(session: AsyncSession, provider_id: str, text: str) -> InboundReply | None:
    """A late transcript (e.g. Twilio's transcription callback) for a voicemail we already stored."""
    reply = await session.scalar(select(InboundReply).where(InboundReply.provider_message_id == provider_id))
    if reply is None or not text.strip():
        return reply
    from app.services.transcription import PLACEHOLDER

    if reply.body.strip() and reply.body != PLACEHOLDER:
        return reply  # already have a real transcript
    reply.body = reply.raw_body = text[:20000]
    if reply.classified_by != "manual":
        reply.classification = None  # re-triage with the real words
    await session.commit()
    try:
        await queue.enqueue_reply(reply.id)
    except Exception:
        await process_reply(session, reply.id)
    return reply


def twilio_signature_valid(auth_token: str, url: str, params: dict[str, str], signature: str | None) -> bool:
    """https://www.twilio.com/docs/usage/security#validating-requests"""
    if not signature:
        return False
    payload = url + "".join(f"{k}{params[k]}" for k in sorted(params))
    digest = hmac.new(auth_token.encode(), payload.encode(), hashlib.sha1).digest()
    expected = base64.b64encode(digest).decode()
    return hmac.compare_digest(expected, signature)
