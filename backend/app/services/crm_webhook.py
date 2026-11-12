"""Signed outbound webhooks to the customer's CRM (HubSpot / Salesforce / Zapier / anything).

Signature: X-Raven-Signature: sha256=<hex HMAC-SHA256(webhook_secret, f"{timestamp}.{raw_body}")>
Receivers should recompute it and reject timestamps older than ~5 minutes.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
import time
from datetime import datetime, timezone

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import Campaign, Company, InboundReply, MessageVariant, Submission, Workspace

log = logging.getLogger("raven.crm_webhook")

EVENT_POSITIVE = "reply.positive"


def new_secret() -> str:
    return "whsec_" + secrets.token_hex(24)


def ensure_secret(ws: Workspace) -> str:
    if not ws.webhook_secret:
        ws.webhook_secret = new_secret()
    return ws.webhook_secret


def sign(secret: str, timestamp: int, body: bytes) -> str:
    mac = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
    return f"sha256={mac}"


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


async def build_payload(session: AsyncSession, reply: InboundReply, event: str = EVENT_POSITIVE) -> dict:
    company = await session.get(Company, reply.company_id) if reply.company_id else None
    sub = await session.get(Submission, reply.submission_id) if reply.submission_id else None
    campaign = await session.get(Campaign, sub.campaign_id) if sub else None
    variant = await session.get(MessageVariant, sub.variant_id) if sub else None
    return {
        "event": event,
        "sent_at": datetime.now(timezone.utc).isoformat(),
        "workspace_id": str(reply.workspace_id),
        "reply": {
            "id": str(reply.id),
            "channel": reply.channel.value,
            "from_address": reply.from_address,
            "from_name": reply.from_name,
            "subject": reply.subject,
            "body": reply.body,
            "classification": reply.classification.value if reply.classification else None,
            "confidence_score": reply.confidence_score,
            "explanation": reply.explanation,
            "recording_url": reply.recording_url,
            "created_at": _iso(reply.created_at),
        },
        "company": {"id": str(company.id), "domain": company.domain, "company_name": company.company_name, "phone": company.phone}
        if company
        else None,
        "campaign": {"id": str(campaign.id), "name": campaign.name} if campaign else None,
        "variant": {"id": str(variant.id), "label": variant.label} if variant else None,
        "submission": {"id": str(sub.id), "contact_url": sub.contact_url, "submitted_at": _iso(sub.submitted_at)} if sub else None,
    }


async def deliver(ws: Workspace, payload: dict) -> dict:
    """POST the payload. Returns {delivered, status_code, logged_only, error}. Never raises."""
    body = json.dumps(payload, separators=(",", ":"), default=str).encode()
    if not ws.webhook_url:
        log.info("CRM webhook (no URL configured, logged only) workspace=%s payload=%s", ws.id, body.decode())
        return {"delivered": False, "status_code": None, "logged_only": True, "error": None}
    ts = int(time.time())
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Raven-Webhooks/1.0",
        "X-Raven-Event": payload.get("event", ""),
        "X-Raven-Timestamp": str(ts),
        "X-Raven-Signature": sign(ensure_secret(ws), ts, body),
    }
    last_err = None
    for attempt in range(2):
        try:
            async with httpx.AsyncClient(timeout=get_settings().webhook_timeout_seconds) as client:
                resp = await client.post(ws.webhook_url, content=body, headers=headers)
            if resp.status_code < 300:
                return {"delivered": True, "status_code": resp.status_code, "logged_only": False, "error": None}
            last_err = f"HTTP {resp.status_code}"
            if resp.status_code < 500:
                return {"delivered": False, "status_code": resp.status_code, "logged_only": False, "error": last_err}
        except httpx.HTTPError as e:
            last_err = f"{type(e).__name__}: {e}"[:300]
    log.warning("CRM webhook to %s failed: %s", ws.webhook_url, last_err)
    return {"delivered": False, "status_code": None, "logged_only": False, "error": last_err}


async def dispatch_positive(session: AsyncSession, reply: InboundReply, ws: Workspace) -> dict | None:
    """Send the reply.positive event once per reply. Caller commits."""
    if reply.webhook_sent_at is not None:
        return None
    result = await deliver(ws, await build_payload(session, reply))
    if result["delivered"] or result["logged_only"]:
        reply.webhook_sent_at = datetime.now(timezone.utc)
    return result
