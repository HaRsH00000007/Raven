import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, File, HTTPException, UploadFile, status
from sqlalchemy import select

from app.api.deps import DB, CurrentWorkspace
from app.models import Campaign, CampaignStatus, MessageVariant, Submission, SubmissionStatus, Workspace
from app.schemas.api import (
    CampaignCreate,
    CampaignDetail,
    CampaignList,
    CampaignSummary,
    CampaignUpdate,
    LaunchIn,
    LaunchOut,
    TargetsIn,
    TargetsResult,
    VariantIn,
    VariantsReplace,
    VariantStats,
)
from app.services import campaigns as svc
from app.services import stats
from app.workers import queue

router = APIRouter(prefix="/campaigns", tags=["campaigns"])

MAX_CSV_BYTES = 20 * 1024 * 1024


async def _get(db, ws: Workspace, campaign_id: uuid.UUID) -> Campaign:
    c = await db.get(Campaign, campaign_id)
    if c is None or c.workspace_id != ws.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Campaign not found")
    return c


def _summary(c: Campaign, counts: dict) -> dict:
    return {
        "id": c.id,
        "name": c.name,
        "status": c.status,
        "sender_company": c.sender_company,
        "sender_website": c.sender_website,
        "sender_phone": c.sender_phone,
        "dry_run": c.dry_run,
        "created_at": c.created_at,
        "launched_at": c.launched_at,
        **counts,
    }


async def _detail(db, c: Campaign) -> CampaignDetail:
    counts = (await stats.campaign_counts(db, [c.id]))[c.id]
    vcounts = await stats.variant_counts(db, c.id)
    variants = []
    for v in await svc.active_variants(db, c.id):
        vc = vcounts.get(v.id, {"sent": 0, "replies": 0, "positive": 0})
        variants.append(
            VariantStats.model_validate(v).model_copy(
                update={**vc, "positive_rate": round(vc["positive"] / vc["sent"], 6) if vc["sent"] else None}
            )
        )
    return CampaignDetail(**_summary(c, counts), variants=variants)


def _variant_rows(campaign_id: uuid.UUID, variants: list[VariantIn]) -> list[MessageVariant]:
    return [MessageVariant(campaign_id=campaign_id, **v.model_dump()) for v in variants]


@router.get("", response_model=CampaignList)
async def list_campaigns(ws: CurrentWorkspace, db: DB):
    items = list(await db.scalars(select(Campaign).where(Campaign.workspace_id == ws.id).order_by(Campaign.created_at.desc())))
    counts = await stats.campaign_counts(db, [c.id for c in items])
    return {"items": [CampaignSummary(**_summary(c, counts[c.id])) for c in items]}


@router.post("", response_model=CampaignDetail, status_code=201)
async def create_campaign(body: CampaignCreate, ws: CurrentWorkspace, db: DB):
    c = Campaign(
        workspace_id=ws.id,
        name=body.name,
        sender_company=body.sender_company or "",
        sender_website=body.sender_website,
        sender_phone=body.sender_phone,
        status=CampaignStatus.draft,
    )
    db.add(c)
    await db.flush()
    db.add_all(_variant_rows(c.id, body.variants))
    if body.domains:
        await svc.add_targets(db, c, [svc.TargetInput(domain=d) for d in body.domains])
    await db.commit()
    return await _detail(db, c)


@router.get("/{campaign_id}", response_model=CampaignDetail)
async def get_campaign(campaign_id: uuid.UUID, ws: CurrentWorkspace, db: DB):
    return await _detail(db, await _get(db, ws, campaign_id))


@router.patch("/{campaign_id}", response_model=CampaignDetail)
async def update_campaign(campaign_id: uuid.UUID, body: CampaignUpdate, ws: CurrentWorkspace, db: DB):
    c = await _get(db, ws, campaign_id)
    for k, v in body.model_dump(exclude_unset=True).items():
        if k in ("name", "sender_company") and v is None:
            continue  # non-nullable columns
        setattr(c, k, v)
    await db.commit()
    return await _detail(db, c)


@router.put("/{campaign_id}/variants", response_model=CampaignDetail)
async def replace_variants(campaign_id: uuid.UUID, body: VariantsReplace, ws: CurrentWorkspace, db: DB):
    """Archive the current variants and install new ones. Pending submissions are re-pointed to the
    new variant with the same label (or the first new variant) so they send the latest copy."""
    c = await _get(db, ws, campaign_id)
    old = await svc.active_variants(db, c.id)
    now = datetime.now(timezone.utc)
    for v in old:
        v.archived_at = now
    await db.flush()
    new_rows = _variant_rows(c.id, body.variants)
    db.add_all(new_rows)
    await db.flush()
    by_label = {v.label: v for v in new_rows if v.weight > 0}
    fallback = next(v for v in new_rows if v.weight > 0)
    old_label = {v.id: v.label for v in old}
    pending = await db.scalars(
        select(Submission).where(Submission.campaign_id == c.id, Submission.status == SubmissionStatus.PENDING)
    )
    for s in pending:
        s.variant_id = by_label.get(old_label.get(s.variant_id, ""), fallback).id
    await db.commit()
    return await _detail(db, c)


async def _after_targets_added(db, c: Campaign) -> None:
    if c.status is CampaignStatus.running:
        ids = await svc.create_pending_submissions(db, c)
        await db.commit()
        if ids:
            await queue.enqueue_submissions(ids)
    else:
        await db.commit()


@router.post("/{campaign_id}/targets", response_model=TargetsResult)
async def add_targets(campaign_id: uuid.UUID, body: TargetsIn, ws: CurrentWorkspace, db: DB):
    c = await _get(db, ws, campaign_id)
    if c.status is CampaignStatus.completed:
        c.status = CampaignStatus.running if c.launched_at else CampaignStatus.draft
    res = await svc.add_targets(db, c, [svc.TargetInput(domain=d) for d in body.domains])
    await _after_targets_added(db, c)
    return TargetsResult(added=res.added, duplicates=res.duplicates, invalid=res.invalid[:200])


@router.post("/{campaign_id}/targets/csv", response_model=TargetsResult)
async def add_targets_csv(campaign_id: uuid.UUID, ws: CurrentWorkspace, db: DB, file: UploadFile = File(...)):
    c = await _get(db, ws, campaign_id)
    content = await file.read(MAX_CSV_BYTES + 1)
    if len(content) > MAX_CSV_BYTES:
        raise HTTPException(413, "CSV larger than 20 MB")
    targets = svc.parse_domains_csv(content)
    if not targets:
        raise HTTPException(422, "No rows found in CSV")
    if c.status is CampaignStatus.completed:
        c.status = CampaignStatus.running if c.launched_at else CampaignStatus.draft
    res = await svc.add_targets(db, c, targets)
    await _after_targets_added(db, c)
    return TargetsResult(added=res.added, duplicates=res.duplicates, invalid=res.invalid[:200])


@router.post("/{campaign_id}/launch", response_model=LaunchOut)
async def launch(campaign_id: uuid.UUID, body: LaunchIn, ws: CurrentWorkspace, db: DB):
    c = await _get(db, ws, campaign_id)
    if c.status is CampaignStatus.running:
        raise HTTPException(409, "Campaign is already running")
    if c.status is not CampaignStatus.draft:
        raise HTTPException(409, "Use /resume for paused campaigns")
    if not body.dry_run and ws.credits_balance <= 0:
        raise HTTPException(402, "No credits left in this workspace")
    try:
        ids = await svc.launch(db, c, body.dry_run)
    except ValueError as e:
        raise HTTPException(422, str(e))
    await svc.maybe_complete(db, c.id)
    await db.commit()
    queued = await queue.enqueue_submissions(ids) if ids else 0
    return LaunchOut(queued=queued, status=c.status)


@router.post("/{campaign_id}/pause", response_model=CampaignDetail)
async def pause(campaign_id: uuid.UUID, ws: CurrentWorkspace, db: DB):
    c = await _get(db, ws, campaign_id)
    if c.status is not CampaignStatus.running:
        raise HTTPException(409, f"Campaign is {c.status.value}, not running")
    c.status = CampaignStatus.paused
    await db.commit()
    return await _detail(db, c)


@router.post("/{campaign_id}/resume", response_model=LaunchOut)
async def resume(campaign_id: uuid.UUID, ws: CurrentWorkspace, db: DB):
    c = await _get(db, ws, campaign_id)
    if c.status is not CampaignStatus.paused:
        raise HTTPException(409, f"Campaign is {c.status.value}, not paused")
    if not c.dry_run and ws.credits_balance <= 0:
        raise HTTPException(402, "No credits left in this workspace")
    c.status = CampaignStatus.running
    await svc.create_pending_submissions(db, c)
    ids = await svc.pending_submission_ids(db, c.id)
    await svc.maybe_complete(db, c.id)
    await db.commit()
    queued = await queue.enqueue_submissions(ids) if ids else 0
    return LaunchOut(queued=queued, status=c.status)
