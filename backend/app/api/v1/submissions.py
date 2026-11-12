import uuid
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import func, select

from app.api.deps import DB, CurrentWorkspace
from app.core.config import get_settings
from app.models import Campaign, Company, InboundReply, MessageVariant, Submission, SubmissionStatus
from app.schemas.api import SubmissionOut, SubmissionPage

router = APIRouter(prefix="/submissions", tags=["submissions"])


def _base_query(workspace_id: uuid.UUID):
    return (
        select(Submission, Campaign.name, MessageVariant.label, Company.domain, Company.company_name)
        .join(Campaign, Campaign.id == Submission.campaign_id)
        .join(MessageVariant, MessageVariant.id == Submission.variant_id)
        .join(Company, Company.id == Submission.company_id)
        .where(Submission.workspace_id == workspace_id)
    )


async def _reply_info(db, ids: list[uuid.UUID]) -> dict[uuid.UUID, tuple[int, str | None]]:
    if not ids:
        return {}
    counts = dict(
        (await db.execute(
            select(InboundReply.submission_id, func.count())
            .where(InboundReply.submission_id.in_(ids))
            .group_by(InboundReply.submission_id)
        )).all()
    )
    latest: dict[uuid.UUID, str | None] = {}
    rows = await db.execute(
        select(InboundReply.submission_id, InboundReply.classification)
        .where(InboundReply.submission_id.in_(ids))
        .order_by(InboundReply.created_at.asc())
    )
    for sid, cls in rows.all():
        latest[sid] = cls
    return {i: (counts.get(i, 0), latest.get(i)) for i in ids}


def _out(row, info) -> SubmissionOut:
    sub, campaign_name, label, domain, company_name = row
    n, latest = info.get(sub.id, (0, None))
    return SubmissionOut(
        id=sub.id,
        campaign_id=sub.campaign_id,
        campaign_name=campaign_name,
        variant_id=sub.variant_id,
        variant_label=label,
        company_id=sub.company_id,
        domain=domain,
        company_name=company_name,
        contact_url=sub.contact_url,
        unique_reply_email=sub.unique_reply_email,
        status=sub.status,
        detail=sub.detail,
        dry_run=sub.dry_run,
        has_pre_screenshot=bool(sub.pre_screenshot_path),
        has_post_screenshot=bool(sub.post_screenshot_path),
        fields_filled=sub.fields_filled or {},
        created_at=sub.created_at,
        submitted_at=sub.submitted_at,
        reply_count=n,
        latest_classification=latest,
    )


@router.get("", response_model=SubmissionPage)
async def list_submissions(
    ws: CurrentWorkspace,
    db: DB,
    campaign_id: uuid.UUID | None = None,
    status: SubmissionStatus | None = None,
    q: str | None = Query(None, max_length=255),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    stmt = _base_query(ws.id)
    if campaign_id:
        stmt = stmt.where(Submission.campaign_id == campaign_id)
    if status:
        stmt = stmt.where(Submission.status == status)
    if q:
        stmt = stmt.where(Company.domain.ilike(f"%{q.strip().lower()}%"))
    total = await db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = (
        await db.execute(
            stmt.order_by(func.coalesce(Submission.submitted_at, Submission.created_at).desc(), Submission.id)
            .limit(limit)
            .offset(offset)
        )
    ).all()
    info = await _reply_info(db, [r[0].id for r in rows])
    return SubmissionPage(items=[_out(r, info) for r in rows], total=total or 0)


@router.get("/{submission_id}", response_model=SubmissionOut)
async def get_submission(submission_id: uuid.UUID, ws: CurrentWorkspace, db: DB):
    row = (await db.execute(_base_query(ws.id).where(Submission.id == submission_id))).first()
    if row is None:
        raise HTTPException(404, "Submission not found")
    return _out(row, await _reply_info(db, [submission_id]))


@router.get("/{submission_id}/screenshots/{which}")
async def screenshot(submission_id: uuid.UUID, which: Literal["pre", "post"], ws: CurrentWorkspace, db: DB):
    sub = await db.get(Submission, submission_id)
    if sub is None or sub.workspace_id != ws.id:
        raise HTTPException(404, "Submission not found")
    name = sub.pre_screenshot_path if which == "pre" else sub.post_screenshot_path
    if not name:
        raise HTTPException(404, "No screenshot")
    root = get_settings().screenshot_dir.resolve()
    path = (root / Path(name).name).resolve()
    if root not in path.parents or not path.is_file():
        raise HTTPException(404, "Screenshot file missing")
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "private, max-age=86400"})
