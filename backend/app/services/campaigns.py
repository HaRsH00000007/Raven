from __future__ import annotations

import csv
import io
import random
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import (
    Campaign,
    CampaignStatus,
    CampaignTarget,
    Company,
    MessageVariant,
    Submission,
    SubmissionStatus,
)
from app.services.email_utils import reply_address
from app.services.form_rules import normalize_domain

MAX_TARGETS_PER_REQUEST = 100_000


@dataclass
class TargetInput:
    domain: str
    company_name: str | None = None
    phone: str | None = None


@dataclass
class AddTargetsResult:
    added: int = 0
    duplicates: int = 0
    invalid: list[str] = field(default_factory=list)
    new_company_ids: list[uuid.UUID] = field(default_factory=list)


def parse_domains_csv(content: bytes) -> list[TargetInput]:
    text = content.decode("utf-8-sig", errors="replace")
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    rows = list(csv.reader(io.StringIO(text), dialect))
    if not rows:
        return []
    header = [h.strip().lower() for h in rows[0]]
    domain_col = next((header.index(c) for c in ("domain", "website", "url", "site") if c in header), None)
    name_col = next((header.index(c) for c in ("company_name", "company", "name") if c in header), None)
    phone_col = next((header.index(c) for c in ("phone", "phone_number", "telephone") if c in header), None)
    body = rows[1:] if domain_col is not None or name_col is not None else rows
    if domain_col is None:
        domain_col = 0
    out = []
    for r in body:
        if len(r) <= domain_col or not r[domain_col].strip():
            continue
        name = r[name_col].strip() if name_col is not None and len(r) > name_col and name_col != domain_col else None
        phone = r[phone_col].strip() if phone_col is not None and len(r) > phone_col else None
        out.append(TargetInput(domain=r[domain_col].strip(), company_name=name or None, phone=phone or None))
    return out


async def add_targets(
    session: AsyncSession, campaign: Campaign, targets: list[TargetInput]
) -> AddTargetsResult:
    res = AddTargetsResult()
    seen: dict[str, str | None] = {}
    phones: dict[str, str] = {}
    for t in targets[:MAX_TARGETS_PER_REQUEST]:
        d = normalize_domain(t.domain)
        if d is None:
            res.invalid.append(t.domain[:100])
            continue
        if d in seen:
            res.duplicates += 1
            continue
        seen[d] = t.company_name
        digits = "".join(ch for ch in (t.phone or "") if ch.isdigit())
        if len(digits) >= 7:
            phones[d] = digits[:20]
    if not seen:
        return res

    domains = list(seen)
    existing: dict[str, Company] = {}
    for chunk_start in range(0, len(domains), 1000):
        chunk = domains[chunk_start : chunk_start + 1000]
        rows = await session.scalars(
            select(Company).where(Company.workspace_id == campaign.workspace_id, Company.domain.in_(chunk))
        )
        existing.update({c.domain: c for c in rows})

    for d in domains:
        if d not in existing:
            c = Company(workspace_id=campaign.workspace_id, domain=d, company_name=seen[d])
            session.add(c)
            existing[d] = c
        elif seen[d] and not existing[d].company_name:
            existing[d].company_name = seen[d]
        if d in phones:
            existing[d].phone = phones[d]
    await session.flush()

    company_ids = [existing[d].id for d in domains]
    already = set()
    for chunk_start in range(0, len(company_ids), 1000):
        chunk = company_ids[chunk_start : chunk_start + 1000]
        already.update(
            await session.scalars(
                select(CampaignTarget.company_id).where(
                    CampaignTarget.campaign_id == campaign.id, CampaignTarget.company_id.in_(chunk)
                )
            )
        )
    for cid in company_ids:
        if cid in already:
            res.duplicates += 1
            continue
        session.add(CampaignTarget(campaign_id=campaign.id, company_id=cid))
        res.added += 1
        res.new_company_ids.append(cid)
    await session.flush()
    return res


def _variant_picker(variants: list[MessageVariant], seed: str):
    pool = [v for v in variants for _ in range(max(0, v.weight))] or list(variants)
    rng = random.Random(seed)
    while True:
        bag = pool[:]
        rng.shuffle(bag)
        yield from bag


async def active_variants(session: AsyncSession, campaign_id: uuid.UUID) -> list[MessageVariant]:
    return list(
        await session.scalars(
            select(MessageVariant)
            .where(MessageVariant.campaign_id == campaign_id, MessageVariant.archived_at.is_(None))
            .order_by(MessageVariant.label)
        )
    )


async def create_pending_submissions(session: AsyncSession, campaign: Campaign) -> list[uuid.UUID]:
    """One submission per target that doesn't have one yet; variants assigned by weighted shuffle."""
    variants = await active_variants(session, campaign.id)
    if not variants:
        raise ValueError("Campaign has no active message variants")
    targets = list(
        await session.scalars(
            select(CampaignTarget.company_id)
            .where(CampaignTarget.campaign_id == campaign.id)
            .where(
                ~select(Submission.id)
                .where(Submission.campaign_id == campaign.id, Submission.company_id == CampaignTarget.company_id)
                .exists()
            )
            .order_by(CampaignTarget.created_at)
        )
    )
    picker = _variant_picker(variants, seed=f"{campaign.id}:{len(targets)}")
    reply_domain = get_settings().reply_domain
    ids = []
    for company_id in targets:
        sid = uuid.uuid4()
        session.add(
            Submission(
                id=sid,
                workspace_id=campaign.workspace_id,
                campaign_id=campaign.id,
                company_id=company_id,
                variant_id=next(picker).id,
                unique_reply_email=reply_address(sid, reply_domain),
                status=SubmissionStatus.PENDING,
                dry_run=campaign.dry_run,
            )
        )
        ids.append(sid)
    await session.flush()
    return ids


async def pending_submission_ids(session: AsyncSession, campaign_id: uuid.UUID) -> list[uuid.UUID]:
    return list(
        await session.scalars(
            select(Submission.id).where(
                Submission.campaign_id == campaign_id, Submission.status == SubmissionStatus.PENDING
            )
        )
    )


async def launch(session: AsyncSession, campaign: Campaign, dry_run: bool) -> list[uuid.UUID]:
    if campaign.status is CampaignStatus.draft:
        campaign.dry_run = dry_run
        campaign.launched_at = datetime.now(timezone.utc)
    campaign.status = CampaignStatus.running
    await create_pending_submissions(session, campaign)
    return await pending_submission_ids(session, campaign.id)


async def maybe_complete(session: AsyncSession, campaign_id: uuid.UUID) -> None:
    campaign = await session.get(Campaign, campaign_id)
    if campaign is None or campaign.status is not CampaignStatus.running:
        return
    pending = await session.scalar(
        select(func.count())
        .select_from(Submission)
        .where(Submission.campaign_id == campaign_id, Submission.status == SubmissionStatus.PENDING)
    )
    if not pending:
        campaign.status = CampaignStatus.completed
