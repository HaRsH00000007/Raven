"""Reply triage: classify an inbound reply, then act on the label.

  positive  -> forward to the workspace's sales inboxes
  not_sure  -> forward only if the workspace opted in
  negative  -> add the company to the do-not-contact list (and forward if opted in)
  automatic -> stays in Raven

Every positive reply is also POSTed (signed) to the workspace's CRM webhook, or logged when none is set.
Voicemails arrive without a transcript when only a recording exists; they are transcribed here first.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import session_factory
from app.models import (
    Campaign,
    Classification,
    Company,
    DoNotContact,
    InboundReply,
    MessageVariant,
    Submission,
    Workspace,
)
from app.services import crm_webhook, llm, transcription
from app.services.mailer import send_email
from app.services.triage_rules import classify_reply_rules

log = logging.getLogger("raven.triage")


async def classify(reply: InboundReply) -> tuple[Classification, float, str, str]:
    """Returns (classification, confidence, explanation, classified_by)."""
    rules = classify_reply_rules(
        body=reply.body,
        subject=reply.subject,
        from_address=reply.from_address,
        auto_header=reply.is_auto_submitted,
        channel=reply.channel.value,
    )
    # Header-flagged auto replies and SMS STOP are unambiguous; don't spend an LLM call.
    if rules.confidence >= 0.95 or not llm.llm_enabled():
        return Classification(rules.classification), rules.confidence, rules.explanation, "rules"
    try:
        out = await llm.classify_reply_llm(
            channel=reply.channel.value, subject=reply.subject, body=reply.body, from_address=reply.from_address
        )
        return Classification(out.classification), out.confidence, out.explanation, "llm"
    except Exception as e:  # network, rate limit, refusal: rules are always available
        log.warning("LLM triage failed for reply %s, using rules: %s", reply.id, e)
        return Classification(rules.classification), rules.confidence, rules.explanation, "rules"


def should_forward(ws: Workspace, c: Classification) -> bool:
    if c is Classification.positive:
        return True
    if c is Classification.not_sure:
        return ws.forward_not_sure
    if c is Classification.negative:
        return ws.forward_negative
    return False


async def forward_reply(session: AsyncSession, reply: InboundReply, ws: Workspace) -> bool:
    if not ws.forward_emails:
        return False
    company = await session.get(Company, reply.company_id) if reply.company_id else None
    sub = await session.get(Submission, reply.submission_id) if reply.submission_id else None
    campaign = await session.get(Campaign, sub.campaign_id) if sub else None
    variant = await session.get(MessageVariant, sub.variant_id) if sub else None
    label = (reply.classification.value if reply.classification else "reply").replace("_", " ").title()
    who = company.company_name or company.domain if company else (reply.from_address or "unknown sender")
    lines = [
        f"{label} reply from {who}",
        "",
        f"Channel: {reply.channel.value}",
        f"From: {reply.from_name or ''} <{reply.from_address or ''}>".strip(),
    ]
    if company:
        lines.append(f"Company: {company.domain}")
    if campaign:
        lines.append(f"Campaign: {campaign.name} (variant {variant.label if variant else '?'})")
    if reply.explanation:
        lines.append(f"Why: {reply.explanation}")
    if reply.recording_url:
        lines.append(f"Recording: {reply.recording_url}")
    lines += ["", "-" * 40, reply.body or reply.raw_body]
    ok = await send_email(
        to=ws.forward_emails,
        subject=f"[Raven · {label}] {who}",
        text="\n".join(lines),
        reply_to=reply.from_address if reply.channel.value == "email" else None,
    )
    if ok:
        reply.is_forwarded = True
    return ok


async def add_to_dnc(session: AsyncSession, workspace_id: uuid.UUID, company_id: uuid.UUID | None, reason: str) -> None:
    if company_id is None:
        return
    company = await session.get(Company, company_id)
    if company is None:
        return
    company.is_blacklisted = True
    company.blacklist_reason = reason
    exists = await session.scalar(
        select(DoNotContact.id).where(DoNotContact.workspace_id == workspace_id, DoNotContact.pattern == company.domain)
    )
    if not exists:
        session.add(DoNotContact(workspace_id=workspace_id, pattern=company.domain, reason=reason))


async def process_reply(session: AsyncSession, reply_id: uuid.UUID) -> InboundReply | None:
    reply = await session.get(InboundReply, reply_id)
    if reply is None:
        return None
    ws = await session.get(Workspace, reply.workspace_id)
    assert ws is not None
    if reply.channel.value == "voicemail" and not reply.body.strip():
        text = await transcription.transcribe(reply.recording_url or "")
        reply.body = reply.raw_body = text or transcription.PLACEHOLDER
    if reply.classification is None:
        c, conf, why, by = await classify(reply)
        reply.classification, reply.confidence_score, reply.explanation, reply.classified_by = c, conf, why, by
    if reply.classification is Classification.negative:
        await add_to_dnc(session, ws.id, reply.company_id, "negative_reply")
    if not reply.is_forwarded and should_forward(ws, reply.classification):
        await forward_reply(session, reply, ws)
    if reply.classification is Classification.positive:
        await crm_webhook.dispatch_positive(session, reply, ws)
    await session.commit()
    return reply


async def triage_reply(ctx: dict, reply_id: str) -> str | None:
    """ARQ job."""
    async with session_factory()() as session:
        reply = await process_reply(session, uuid.UUID(reply_id))
        return reply.classification.value if reply and reply.classification else None
