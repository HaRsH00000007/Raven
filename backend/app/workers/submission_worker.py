"""ARQ job that runs one submission end to end: compliance checks -> form engine -> billing."""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import session_factory
from app.models import (
    Campaign,
    CampaignStatus,
    Company,
    DoNotContact,
    MessageVariant,
    Submission,
    SubmissionStatus,
    Workspace,
)
from app.services import credits, llm
from app.services.campaigns import maybe_complete
from app.services.form_rules import dnc_matches, render_template
from app.workers.form_probe import FormEngine, NoCreditsError, ProbeOptions, ProbeResult, RobotsChecker, SenderProfile

log = logging.getLogger("raven.submission")

PROBE_TIMEOUT_S = 150
# Statuses that mean the company actually received (or may have received) our message.
CONTACTED = {SubmissionStatus.SUCCESS_VERIFIED, SubmissionStatus.SUBMITTED_UNVERIFIED}


def is_billable(status: SubmissionStatus) -> bool:
    if status is SubmissionStatus.SUCCESS_VERIFIED:
        return True
    return status is SubmissionStatus.SUBMITTED_UNVERIFIED and get_settings().bill_unverified


def _aware(dt: datetime | None) -> datetime | None:
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


async def _finish(session, sub: Submission, status: SubmissionStatus, detail: str) -> None:
    sub.status = status
    sub.detail = detail
    await session.flush()
    await maybe_complete(session, sub.campaign_id)
    await session.commit()


async def process_submission(ctx: dict, submission_id: str) -> str:
    settings = get_settings()
    engine: FormEngine = ctx["engine"]
    robots: RobotsChecker = ctx["robots"]
    sid = uuid.UUID(submission_id)
    Session = session_factory()

    async with Session() as session:
        sub = await session.get(Submission, sid)
        if sub is None or sub.status is not SubmissionStatus.PENDING:
            return "noop"
        campaign = await session.get(Campaign, sub.campaign_id)
        if campaign is None or campaign.status is not CampaignStatus.running:
            return "campaign-not-running"  # stays PENDING; resume re-enqueues it
        company = await session.get(Company, sub.company_id)
        variant = await session.get(MessageVariant, sub.variant_id)
        assert company is not None and variant is not None

        # --- compliance gates (no browser needed) ------------------------------------
        patterns = list(await session.scalars(select(DoNotContact.pattern).where(DoNotContact.workspace_id == sub.workspace_id)))
        hit = next((p for p in patterns if dnc_matches(company.domain, p)), None)
        if company.is_blacklisted or hit:
            reason = f"Do-not-contact: {hit}" if hit else f"Company blacklisted ({company.blacklist_reason})"
            await _finish(session, sub, SubmissionStatus.SKIPPED_DNC, reason)
            return sub.status.value
        last = _aware(company.last_contacted_at)
        if not sub.dry_run and last and last > datetime.now(timezone.utc) - timedelta(days=settings.cooldown_days):
            await _finish(session, sub, SubmissionStatus.SKIPPED_COOLDOWN, f"Contacted {last.date().isoformat()}; {settings.cooldown_days}-day cooldown")
            return sub.status.value

        ws = await session.get(Workspace, sub.workspace_id)
        values = {
            "company_name": company.company_name or company.domain,
            "domain": company.domain,
            "sender_name": variant.sender_name,
            "sender_company": campaign.sender_company,
        }
        sender = SenderProfile(
            full_name=variant.sender_name,
            email=sub.unique_reply_email,
            message=render_template(variant.message_body, values),
            subject=render_template(variant.subject, values) if variant.subject else None,
            phone=campaign.sender_phone or (ws.default_sender_phone if ws else None),
            company=campaign.sender_company or None,
            title=variant.sender_title,
            website=campaign.sender_website,
        )
        sub.attempts += 1
        await session.commit()
        workspace_id, campaign_id, domain, dry_run = sub.workspace_id, sub.campaign_id, company.domain, sub.dry_run

    charged = False

    async def before_submit() -> bool:
        nonlocal charged
        async with Session() as s:
            charged = await credits.reserve(s, workspace_id, sid)
        return charged

    opts = ProbeOptions(
        run_id=str(sid),
        screenshot_dir=settings.screenshot_dir,
        dry_run=dry_run,
        page_timeout_ms=settings.page_timeout_ms,
        submit_wait_ms=settings.submit_wait_ms,
        ua_token=settings.bot_user_agent_token,
        bot_info_url=settings.bot_info_url,
    )
    field_mapper = llm.map_form_fields_llm if llm.llm_enabled() else None

    try:
        result = await asyncio.wait_for(
            engine.probe(domain, sender, opts, robots, before_submit=before_submit, field_mapper=field_mapper),
            timeout=PROBE_TIMEOUT_S,
        )
    except NoCreditsError:
        async with Session() as session:
            camp = await session.get(Campaign, campaign_id)
            if camp and camp.status is CampaignStatus.running:
                camp.status = CampaignStatus.paused
            await session.commit()
        log.warning("Workspace %s out of credits; paused campaign %s", workspace_id, campaign_id)
        return "no-credits"
    except asyncio.TimeoutError:
        result = ProbeResult(
            status=SubmissionStatus.FAILED_ERROR, detail=f"Timed out after {PROBE_TIMEOUT_S}s", submit_clicked=charged
        )

    async with Session() as session:
        sub = await session.get(Submission, sid)
        company = await session.get(Company, sub.company_id)
        sub.status = result.status
        sub.detail = result.detail[:2000]
        sub.contact_url = result.contact_url
        sub.fields_filled = result.fields_filled
        sub.pre_screenshot_path = result.pre_screenshot
        sub.post_screenshot_path = result.post_screenshot
        if result.submit_clicked:
            sub.submitted_at = datetime.now(timezone.utc)

        billable = is_billable(result.status)
        if charged and not billable:
            await credits.refund(session, workspace_id, sid, f"{result.status.value}: {result.detail}")
        sub.credit_charged = charged and billable

        if result.contact_url:
            company.contact_url = result.contact_url
        if result.status in CONTACTED:
            company.last_contacted_at = datetime.now(timezone.utc)
        if result.status is SubmissionStatus.SKIPPED_NO_SOLICITATION:
            company.is_blacklisted = True
            company.blacklist_reason = "no_solicitation"

        await session.flush()
        await maybe_complete(session, campaign_id)
        await session.commit()

    log.info("submission %s %s -> %s (%s) in %.1fs", sid, domain, result.status.value, result.detail, result.duration_s)
    return result.status.value
