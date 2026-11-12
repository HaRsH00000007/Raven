"""Credit accounting.

The spendable balance lives on `workspaces.credits_balance`; every change is mirrored in `credit_ledger`
so the balance is always explainable.

Expiry model (matches the proposal's "unused forms carry over one month"):
  * Each grant row has `expires_at` (default: CREDIT_VALIDITY_DAYS = 60 days = billing month + 1 rollover month)
    and `remaining` (how much of that grant is still unused).
  * Usage is drawn FIFO from the unexpired grant that expires first; the `used` row records `grant_id`, and a
    refund returns the credit to that grant if it hasn't expired yet.
  * `expire_due_credits` removes each expired grant's `remaining` from the balance and writes an `expired` row.
Grants with `remaining IS NULL` (created before expiry tracking) never expire.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import CreditLedger, LedgerAction, Workspace

log = logging.getLogger("raven.credits")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


async def grant(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    amount: int,
    note: str | None = None,
    action: LedgerAction = LedgerAction.grant,
    expires_in_days: int | None = -1,
) -> CreditLedger:
    """Add (or, with a negative amount, remove) credits. Caller commits.

    expires_in_days: -1 = use CREDIT_VALIDITY_DAYS, None = never expires. Only positive grants expire.
    """
    await session.execute(
        update(Workspace)
        .where(Workspace.id == workspace_id)
        .values(credits_balance=Workspace.credits_balance + amount)
    )
    row = CreditLedger(workspace_id=workspace_id, amount=amount, action=action, note=note)
    if amount > 0:
        days = get_settings().credit_validity_days if expires_in_days == -1 else expires_in_days
        row.remaining = amount
        row.expires_at = _now() + timedelta(days=days) if days is not None else None
    elif amount < 0:
        # A manual deduction consumes the oldest grants first, like usage.
        await _consume(session, workspace_id, -amount)
    session.add(row)
    await session.flush()
    return row


async def _open_grants(session: AsyncSession, workspace_id: uuid.UUID, lock: bool = True) -> list[CreditLedger]:
    now = _now()
    stmt = (
        select(CreditLedger)
        .where(
            CreditLedger.workspace_id == workspace_id,
            CreditLedger.remaining > 0,
            CreditLedger.expired_at.is_(None),
            or_(CreditLedger.expires_at.is_(None), CreditLedger.expires_at > now),
        )
        # Soonest-expiring first; never-expiring grants last.
        .order_by(CreditLedger.expires_at.is_(None), CreditLedger.expires_at, CreditLedger.created_at)
    )
    if lock:
        stmt = stmt.with_for_update()
    return list(await session.scalars(stmt))


async def _consume(session: AsyncSession, workspace_id: uuid.UUID, n: int) -> uuid.UUID | None:
    """Take n credits from open grants FIFO. Returns the grant the first credit came from."""
    first: uuid.UUID | None = None
    for g in await _open_grants(session, workspace_id):
        if n <= 0:
            break
        take = min(n, g.remaining or 0)
        g.remaining = (g.remaining or 0) - take
        n -= take
        first = first or g.id
    return first


async def reserve(session: AsyncSession, workspace_id: uuid.UUID, submission_id: uuid.UUID) -> bool:
    """Atomically take one credit. Returns False when the balance is zero. Commits."""
    result = await session.execute(
        update(Workspace)
        .where(Workspace.id == workspace_id, Workspace.credits_balance > 0)
        .values(credits_balance=Workspace.credits_balance - 1)
    )
    if result.rowcount != 1:
        await session.rollback()
        return False
    grant_id = await _consume(session, workspace_id, 1)
    session.add(
        CreditLedger(
            workspace_id=workspace_id,
            amount=-1,
            action=LedgerAction.used,
            submission_id=submission_id,
            grant_id=grant_id,
        )
    )
    await session.commit()
    return True


async def refund(session: AsyncSession, workspace_id: uuid.UUID, submission_id: uuid.UUID, note: str) -> None:
    """Return a reserved credit for a form that did not send. Caller commits."""
    used = await session.scalar(
        select(CreditLedger)
        .where(CreditLedger.submission_id == submission_id, CreditLedger.action == LedgerAction.used)
        .order_by(CreditLedger.created_at.desc())
        .limit(1)
    )
    grant_row = await session.get(CreditLedger, used.grant_id) if used and used.grant_id else None
    if grant_row is not None and grant_row.expired_at is not None:
        # The grant already expired while the job ran; its credit is gone, so don't resurrect it.
        session.add(
            CreditLedger(
                workspace_id=workspace_id, amount=0, action=LedgerAction.refund_skip,
                submission_id=submission_id, grant_id=grant_row.id, note=f"(grant expired) {note}"[:500],
            )
        )
        return
    await session.execute(
        update(Workspace)
        .where(Workspace.id == workspace_id)
        .values(credits_balance=Workspace.credits_balance + 1)
    )
    if grant_row is not None and grant_row.remaining is not None:
        grant_row.remaining += 1
    session.add(
        CreditLedger(
            workspace_id=workspace_id,
            amount=1,
            action=LedgerAction.refund_skip,
            submission_id=submission_id,
            grant_id=grant_row.id if grant_row else None,
            note=note[:500],
        )
    )


@dataclass
class ExpiryResult:
    expired_grants: int = 0
    credits_expired: int = 0


async def expire_due_credits(session: AsyncSession, now: datetime | None = None) -> ExpiryResult:
    """Expire every grant whose expires_at has passed. Idempotent. Commits."""
    now = now or _now()
    res = ExpiryResult()
    due = list(
        await session.scalars(
            select(CreditLedger)
            .where(
                CreditLedger.action == LedgerAction.grant,
                CreditLedger.expired_at.is_(None),
                CreditLedger.expires_at.is_not(None),
                CreditLedger.expires_at <= now,
            )
            .with_for_update(skip_locked=True)
        )
    )
    for g in due:
        unused = max(0, g.remaining or 0)
        g.expired_at = now
        res.expired_grants += 1
        if unused == 0:
            continue
        ws = await session.get(Workspace, g.workspace_id, with_for_update=True)
        take = min(unused, max(0, ws.credits_balance))  # never push the balance negative
        ws.credits_balance -= take
        g.remaining = 0
        res.credits_expired += take
        session.add(
            CreditLedger(
                workspace_id=g.workspace_id,
                amount=-take,
                action=LedgerAction.expired,
                grant_id=g.id,
                note=f"{take} unused credit(s) from grant of {g.created_at:%Y-%m-%d} expired",
            )
        )
    await session.commit()
    if res.expired_grants:
        log.info("Expired %d grant(s), %d credit(s)", res.expired_grants, res.credits_expired)
    return res
