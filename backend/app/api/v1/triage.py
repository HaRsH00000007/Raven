import uuid

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select

from app.api.deps import DB, CurrentWorkspace
from app.models import Campaign, Classification, Company, InboundReply, MessageVariant, Submission, Workspace
from app.schemas.api import ReplyOut, ReplyPage, ReplyUpdate
from app.services import crm_webhook
from app.workers.triage_worker import add_to_dnc, forward_reply

router = APIRouter(prefix="/replies", tags=["replies"])


def _query(workspace_id: uuid.UUID):
    return (
        select(InboundReply, Company.domain, Company.company_name, Campaign.id, Campaign.name, MessageVariant.label)
        .outerjoin(Company, Company.id == InboundReply.company_id)
        .outerjoin(Submission, Submission.id == InboundReply.submission_id)
        .outerjoin(Campaign, Campaign.id == Submission.campaign_id)
        .outerjoin(MessageVariant, MessageVariant.id == Submission.variant_id)
        .where(InboundReply.workspace_id == workspace_id)
    )


def _out(row) -> ReplyOut:
    r, domain, company_name, campaign_id, campaign_name, label = row
    return ReplyOut(
        id=r.id,
        submission_id=r.submission_id,
        workspace_id=r.workspace_id,
        company_id=r.company_id,
        domain=domain,
        company_name=company_name,
        campaign_id=campaign_id,
        campaign_name=campaign_name,
        variant_label=label,
        channel=r.channel,
        from_address=r.from_address,
        from_name=r.from_name,
        subject=r.subject,
        body=r.body,
        raw_body=r.raw_body,
        recording_url=r.recording_url,
        classification=r.classification,
        confidence_score=r.confidence_score,
        explanation=r.explanation,
        classified_by=r.classified_by,
        is_forwarded=r.is_forwarded,
        call_booked=r.call_booked,
        created_at=r.created_at,
    )


async def _one(db, ws: Workspace, reply_id: uuid.UUID) -> ReplyOut:
    row = (await db.execute(_query(ws.id).where(InboundReply.id == reply_id))).first()
    if row is None:
        raise HTTPException(404, "Reply not found")
    return _out(row)


@router.get("", response_model=ReplyPage)
async def list_replies(
    ws: CurrentWorkspace,
    db: DB,
    classification: Classification | None = None,
    campaign_id: uuid.UUID | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    base = _query(ws.id)
    if campaign_id:
        base = base.where(Submission.campaign_id == campaign_id)
    counts_q = select(InboundReply.classification, func.count()).where(InboundReply.workspace_id == ws.id)
    if campaign_id:
        counts_q = counts_q.join(Submission, Submission.id == InboundReply.submission_id).where(
            Submission.campaign_id == campaign_id
        )
    raw_counts = dict((await db.execute(counts_q.group_by(InboundReply.classification))).all())
    counts = {c.value: int(raw_counts.get(c, 0)) for c in Classification}
    if classification:
        base = base.where(InboundReply.classification == classification)
    total = await db.scalar(select(func.count()).select_from(base.subquery()))
    rows = (await db.execute(base.order_by(InboundReply.created_at.desc(), InboundReply.id).limit(limit).offset(offset))).all()
    return ReplyPage(items=[_out(r) for r in rows], total=total or 0, counts=counts)


@router.get("/{reply_id}", response_model=ReplyOut)
async def get_reply(reply_id: uuid.UUID, ws: CurrentWorkspace, db: DB):
    return await _one(db, ws, reply_id)


@router.patch("/{reply_id}", response_model=ReplyOut)
async def update_reply(reply_id: uuid.UUID, body: ReplyUpdate, ws: CurrentWorkspace, db: DB):
    reply = await db.get(InboundReply, reply_id)
    if reply is None or reply.workspace_id != ws.id:
        raise HTTPException(404, "Reply not found")
    if body.classification is not None and body.classification != reply.classification:
        reply.classification = body.classification
        reply.classified_by = "manual"
        reply.confidence_score = 1.0
        reply.explanation = "Set manually"
        if body.classification is Classification.negative:
            await add_to_dnc(db, ws.id, reply.company_id, "negative_reply")
        if body.classification is Classification.positive:
            await crm_webhook.dispatch_positive(db, reply, ws)
    if body.call_booked is not None:
        reply.call_booked = body.call_booked
    await db.commit()
    return await _one(db, ws, reply_id)


@router.post("/{reply_id}/forward", response_model=ReplyOut)
async def forward(reply_id: uuid.UUID, ws: CurrentWorkspace, db: DB):
    reply = await db.get(InboundReply, reply_id)
    if reply is None or reply.workspace_id != ws.id:
        raise HTTPException(404, "Reply not found")
    if not ws.forward_emails:
        raise HTTPException(422, "Add a forwarding email in Settings first")
    ok = await forward_reply(db, reply, ws)
    if not ok:
        raise HTTPException(502, "Email could not be sent (check SMTP settings)")
    await db.commit()
    return await _one(db, ws, reply_id)
