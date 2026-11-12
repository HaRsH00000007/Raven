from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import Select, distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    CampaignTarget,
    Classification,
    InboundReply,
    Submission,
    SubmissionStatus,
    Workspace,
)
from app.schemas.api import DailyPoint, FunnelStage, OverviewStats

SENT_STATUSES = (SubmissionStatus.SUCCESS_VERIFIED, SubmissionStatus.SUBMITTED_UNVERIFIED)
HUMAN_REPLY = (InboundReply.classification.is_(None)) | (InboundReply.classification != Classification.automatic)


def _rate(n: int, d: int) -> float | None:
    return round(n / d, 6) if d else None


async def _grouped(session: AsyncSession, stmt: Select) -> dict:
    return {k: v for k, v in (await session.execute(stmt)).all()}


async def campaign_counts(session: AsyncSession, campaign_ids: list[uuid.UUID]) -> dict[uuid.UUID, dict[str, int]]:
    out: dict[uuid.UUID, dict[str, int]] = defaultdict(lambda: {"targets": 0, "sent": 0, "pending": 0, "replies": 0, "positive": 0})
    if not campaign_ids:
        return out
    targets = await _grouped(
        session,
        select(CampaignTarget.campaign_id, func.count()).where(CampaignTarget.campaign_id.in_(campaign_ids)).group_by(CampaignTarget.campaign_id),
    )
    sent = await _grouped(
        session,
        select(Submission.campaign_id, func.count())
        .where(Submission.campaign_id.in_(campaign_ids), Submission.status.in_(SENT_STATUSES))
        .group_by(Submission.campaign_id),
    )
    pending = await _grouped(
        session,
        select(Submission.campaign_id, func.count())
        .where(Submission.campaign_id.in_(campaign_ids), Submission.status == SubmissionStatus.PENDING)
        .group_by(Submission.campaign_id),
    )
    replies = await _grouped(
        session,
        select(Submission.campaign_id, func.count(distinct(InboundReply.submission_id)))
        .join(InboundReply, InboundReply.submission_id == Submission.id)
        .where(Submission.campaign_id.in_(campaign_ids), HUMAN_REPLY)
        .group_by(Submission.campaign_id),
    )
    positive = await _grouped(
        session,
        select(Submission.campaign_id, func.count(distinct(InboundReply.submission_id)))
        .join(InboundReply, InboundReply.submission_id == Submission.id)
        .where(Submission.campaign_id.in_(campaign_ids), InboundReply.classification == Classification.positive)
        .group_by(Submission.campaign_id),
    )
    for cid in campaign_ids:
        out[cid] = {
            "targets": targets.get(cid, 0),
            "sent": sent.get(cid, 0),
            "pending": pending.get(cid, 0),
            "replies": replies.get(cid, 0),
            "positive": positive.get(cid, 0),
        }
    return out


async def variant_counts(session: AsyncSession, campaign_id: uuid.UUID) -> dict[uuid.UUID, dict[str, int]]:
    sent = await _grouped(
        session,
        select(Submission.variant_id, func.count())
        .where(Submission.campaign_id == campaign_id, Submission.status.in_(SENT_STATUSES))
        .group_by(Submission.variant_id),
    )
    replies = await _grouped(
        session,
        select(Submission.variant_id, func.count(distinct(InboundReply.submission_id)))
        .join(InboundReply, InboundReply.submission_id == Submission.id)
        .where(Submission.campaign_id == campaign_id, HUMAN_REPLY)
        .group_by(Submission.variant_id),
    )
    positive = await _grouped(
        session,
        select(Submission.variant_id, func.count(distinct(InboundReply.submission_id)))
        .join(InboundReply, InboundReply.submission_id == Submission.id)
        .where(Submission.campaign_id == campaign_id, InboundReply.classification == Classification.positive)
        .group_by(Submission.variant_id),
    )
    keys = set(sent) | set(replies) | set(positive)
    return {k: {"sent": sent.get(k, 0), "replies": replies.get(k, 0), "positive": positive.get(k, 0)} for k in keys}


def _as_date(v) -> str:
    if isinstance(v, (date, datetime)):
        return v.isoformat()[:10]
    return str(v)[:10]


async def overview(session: AsyncSession, ws: Workspace, campaign_id: uuid.UUID | None) -> OverviewStats:
    sub_filter = [Submission.workspace_id == ws.id]
    if campaign_id:
        sub_filter.append(Submission.campaign_id == campaign_id)

    status_rows = await _grouped(
        session, select(Submission.status, func.count()).where(*sub_filter).group_by(Submission.status)
    )
    status_breakdown = {s.value: int(status_rows.get(s, 0)) for s in SubmissionStatus}
    forms_sent = sum(status_breakdown[s.value] for s in SENT_STATUSES)
    forms_attempted = sum(
        v for k, v in status_breakdown.items() if k not in (SubmissionStatus.PENDING.value, SubmissionStatus.DRY_RUN.value)
    )
    credits_used = int(
        await session.scalar(select(func.count()).select_from(Submission).where(*sub_filter, Submission.credit_charged.is_(True)))
        or 0
    )

    reply_base = select(InboundReply.classification, func.count()).where(InboundReply.workspace_id == ws.id)
    if campaign_id:
        reply_base = reply_base.join(Submission, Submission.id == InboundReply.submission_id).where(
            Submission.campaign_id == campaign_id
        )
    by_class = await _grouped(session, reply_base.group_by(InboundReply.classification))
    counts = {c.value: int(by_class.get(c, 0)) for c in Classification}
    human_replies = sum(v for k, v in counts.items() if k != Classification.automatic.value) + int(by_class.get(None, 0))

    booked_q = select(func.count()).select_from(InboundReply).where(InboundReply.workspace_id == ws.id, InboundReply.call_booked.is_(True))
    if campaign_id:
        booked_q = booked_q.join(Submission, Submission.id == InboundReply.submission_id).where(Submission.campaign_id == campaign_id)
    calls_booked = int(await session.scalar(booked_q) or 0)

    if campaign_id:
        targets = int(await session.scalar(select(func.count()).select_from(CampaignTarget).where(CampaignTarget.campaign_id == campaign_id)) or 0)
    else:
        targets = int(await session.scalar(select(func.count(distinct(Submission.company_id))).where(*sub_filter)) or 0)

    spend = credits_used * ws.price_per_form_cents
    positive = counts["positive"]

    since = datetime.now(timezone.utc) - timedelta(days=29)
    since = since.replace(hour=0, minute=0, second=0, microsecond=0)
    daily_sent = await _grouped(
        session,
        select(func.date(Submission.submitted_at), func.count())
        .where(*sub_filter, Submission.status.in_(SENT_STATUSES), Submission.submitted_at >= since)
        .group_by(func.date(Submission.submitted_at)),
    )
    reply_daily_q = (
        select(func.date(InboundReply.created_at), InboundReply.classification, func.count())
        .where(InboundReply.workspace_id == ws.id, InboundReply.created_at >= since)
        .group_by(func.date(InboundReply.created_at), InboundReply.classification)
    )
    if campaign_id:
        reply_daily_q = reply_daily_q.join(Submission, Submission.id == InboundReply.submission_id).where(
            Submission.campaign_id == campaign_id
        )
    daily_replies: dict[str, int] = defaultdict(int)
    daily_positive: dict[str, int] = defaultdict(int)
    for d, cls, n in (await session.execute(reply_daily_q)).all():
        key = _as_date(d)
        if cls is not Classification.automatic:
            daily_replies[key] += n
        if cls is Classification.positive:
            daily_positive[key] += n
    sent_by_day = {_as_date(k): v for k, v in daily_sent.items()}
    daily = []
    for i in range(30):
        day = (since + timedelta(days=i)).date().isoformat()
        daily.append(DailyPoint(date=day, sent=sent_by_day.get(day, 0), replies=daily_replies.get(day, 0), positive=daily_positive.get(day, 0)))

    return OverviewStats(
        forms_attempted=forms_attempted,
        forms_sent=forms_sent,
        replies=human_replies,
        positive=positive,
        not_sure=counts["not_sure"],
        negative=counts["negative"],
        automatic=counts["automatic"],
        calls_booked=calls_booked,
        response_rate=_rate(human_replies, forms_sent),
        positive_rate=_rate(positive, forms_sent),
        credits_used=credits_used,
        spend_cents=spend,
        cost_per_positive_cents=(spend // positive) if positive else None,
        cost_per_reply_cents=(spend // human_replies) if human_replies else None,
        status_breakdown=status_breakdown,
        funnel=[
            FunnelStage(stage="Targets", count=targets),
            FunnelStage(stage="Forms sent", count=forms_sent),
            FunnelStage(stage="Replies", count=human_replies),
            FunnelStage(stage="Positive", count=positive),
            FunnelStage(stage="Calls booked", count=calls_booked),
        ],
        daily=daily,
    )
