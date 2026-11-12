"""Local simulator: create inbound replies without Postmark/Twilio so the full pipeline can be exercised on
localhost. Enabled outside production, or anywhere with ALLOW_SIMULATOR=true."""

import uuid

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.api.deps import DB, CurrentWorkspace
from app.api.v1.triage import _one
from app.core.config import get_settings
from app.models import Company, ReplyChannel, Submission
from app.schemas.api import ReplyOut, SimulateInbound
from app.services.email_utils import reply_address
from app.services.form_rules import normalize_domain
from app.services.inbound import InboundEmail, ingest_email, ingest_phone

router = APIRouter(prefix="/simulate", tags=["simulator"])


def _enabled() -> bool:
    s = get_settings()
    return s.allow_simulator or s.environment != "production"


@router.post("/inbound", response_model=ReplyOut)
async def simulate_inbound(body: SimulateInbound, ws: CurrentWorkspace, db: DB):
    if not _enabled():
        raise HTTPException(403, "Simulator is disabled in production (set ALLOW_SIMULATOR=true to enable)")
    sim_id = f"sim:{uuid.uuid4()}"
    if body.channel == "email":
        sub = None
        if body.submission_id:
            sub = await db.get(Submission, body.submission_id)
            if sub is None or sub.workspace_id != ws.id:
                raise HTTPException(404, "Submission not found")
        elif body.company_domain:
            d = normalize_domain(body.company_domain)
            sub = await db.scalar(
                select(Submission)
                .join(Company, Company.id == Submission.company_id)
                .where(Submission.workspace_id == ws.id, Company.domain == d)
                .order_by(Submission.created_at.desc())
                .limit(1)
            )
        if sub is None:
            raise HTTPException(422, "Email replies need a submission_id or a company_domain that has a submission")
        reply = await ingest_email(
            db,
            InboundEmail(
                recipients=[reply_address(sub.id, get_settings().reply_domain)],
                from_raw=str(body.from_email),
                subject="Re: (simulated)",
                text=body.body,
                html=None,
                message_id=sim_id,
            ),
            inline=True,
        )
    else:
        reply = await ingest_phone(
            db,
            channel=ReplyChannel(body.channel),
            from_number=body.from_number or "",
            body=body.body,
            provider_id=sim_id,
            workspace=ws,
            hint_domain=body.company_domain,
            hint_submission_id=body.submission_id,
            inline=True,
        )
    if reply is None:
        raise HTTPException(422, "Reply could not be stored")
    return await _one(db, ws, reply.id)
