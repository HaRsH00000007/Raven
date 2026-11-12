import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.api.deps import DB, CurrentWorkspace, require_admin
from app.models import CreditLedger, DoNotContact, LedgerAction, Workspace
from app.schemas.api import (
    CreditGrant,
    DncIn,
    ExpiryOut,
    WebhookTestOut,
    DncList,
    DncOut,
    LedgerPage,
    OverviewStats,
    WorkspaceCreate,
    WorkspaceCreated,
    WorkspaceOut,
    WorkspaceUpdate,
)
from app.services import credits, crm_webhook, stats
from app.services.form_rules import normalize_domain
from app.services.security import generate_api_key, hash_api_key

router = APIRouter()


@router.get("/workspace", response_model=WorkspaceOut)
async def get_workspace(ws: CurrentWorkspace):
    return ws


@router.patch("/workspace", response_model=WorkspaceOut)
async def update_workspace(body: WorkspaceUpdate, ws: CurrentWorkspace, db: DB):
    data = body.model_dump(exclude_unset=True)
    if "forward_emails" in data:
        data["forward_emails"] = [str(e).lower() for e in data["forward_emails"] or []]
    for k, v in data.items():
        setattr(ws, k, v)
    if ws.webhook_url:
        crm_webhook.ensure_secret(ws)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "That phone number is already used by another workspace")
    await db.refresh(ws)
    return ws


@router.get("/workspace/ledger", response_model=LedgerPage)
async def ledger(ws: CurrentWorkspace, db: DB, limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0)):
    total = await db.scalar(select(func.count()).select_from(CreditLedger).where(CreditLedger.workspace_id == ws.id))
    items = await db.scalars(
        select(CreditLedger)
        .where(CreditLedger.workspace_id == ws.id)
        .order_by(CreditLedger.created_at.desc(), CreditLedger.id)
        .limit(limit)
        .offset(offset)
    )
    return {"items": list(items), "total": total or 0}


@router.get("/stats/overview", response_model=OverviewStats)
async def overview(ws: CurrentWorkspace, db: DB, campaign_id: uuid.UUID | None = None):
    return await stats.overview(db, ws, campaign_id)


# --- CRM webhook -------------------------------------------------------------------


@router.post("/workspace/webhook/test", response_model=WebhookTestOut)
async def test_webhook(ws: CurrentWorkspace, db: DB):
    payload = {
        "event": crm_webhook.EVENT_POSITIVE,
        "test": True,
        "workspace_id": str(ws.id),
        "reply": {"id": "00000000-0000-0000-0000-000000000000", "channel": "email", "from_address": "jane@example.com",
                  "from_name": "Jane Example", "subject": "Re: quick question",
                  "body": "Yes, we work with RPO partners. When can we talk?", "classification": "positive",
                  "confidence_score": 0.95, "explanation": "Test event", "recording_url": None, "created_at": None},
        "company": {"id": None, "domain": "example.com", "company_name": "Example Staffing", "phone": None},
        "campaign": None, "variant": None, "submission": None,
    }
    if ws.webhook_url:
        crm_webhook.ensure_secret(ws)
        await db.commit()
    result = await crm_webhook.deliver(ws, payload)
    return WebhookTestOut(payload=payload, **result)


@router.post("/workspace/webhook/rotate-secret", response_model=WorkspaceOut)
async def rotate_webhook_secret(ws: CurrentWorkspace, db: DB):
    ws.webhook_secret = crm_webhook.new_secret()
    await db.commit()
    await db.refresh(ws)
    return ws


# --- do-not-contact ----------------------------------------------------------------


@router.get("/do-not-contact", response_model=DncList)
async def list_dnc(ws: CurrentWorkspace, db: DB):
    items = await db.scalars(
        select(DoNotContact).where(DoNotContact.workspace_id == ws.id).order_by(DoNotContact.created_at.desc())
    )
    return {"items": list(items)}


@router.post("/do-not-contact", response_model=DncOut, status_code=201)
async def add_dnc(body: DncIn, ws: CurrentWorkspace, db: DB):
    raw = body.pattern.strip().lower()
    if raw.startswith("*."):
        if not normalize_domain("x" + raw[1:]):
            raise HTTPException(422, "Wildcard must look like *.gov or *.example.com")
        pattern = raw
    else:
        pattern = normalize_domain(raw)
        if not pattern:
            raise HTTPException(422, "Not a valid domain")
    existing = await db.scalar(
        select(DoNotContact).where(DoNotContact.workspace_id == ws.id, DoNotContact.pattern == pattern)
    )
    if existing:
        return existing
    item = DoNotContact(workspace_id=ws.id, pattern=pattern, reason="manual")
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return item


@router.delete("/do-not-contact/{item_id}", status_code=204)
async def delete_dnc(item_id: uuid.UUID, ws: CurrentWorkspace, db: DB):
    item = await db.get(DoNotContact, item_id)
    if item is None or item.workspace_id != ws.id:
        raise HTTPException(404, "Not found")
    await db.delete(item)
    await db.commit()
    return Response(status_code=204)


# --- admin ------------------------------------------------------------------------

admin = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])


@admin.post("/workspaces", response_model=WorkspaceCreated, status_code=201)
async def create_workspace(body: WorkspaceCreate, db: DB):
    key = generate_api_key()
    ws = Workspace(
        name=body.name,
        api_key_hash=hash_api_key(key),
        price_per_form_cents=body.price_per_form_cents,
        forward_emails=[],
    )
    db.add(ws)
    await db.flush()
    if body.credits:
        await credits.grant(db, ws.id, body.credits, note="Initial grant")
    await db.commit()
    return WorkspaceCreated(id=ws.id, name=ws.name, api_key=key)


@admin.post("/workspaces/{workspace_id}/credits", response_model=WorkspaceOut)
async def grant_credits(workspace_id: uuid.UUID, body: CreditGrant, db: DB):
    ws = await db.get(Workspace, workspace_id)
    if ws is None:
        raise HTTPException(404, "Workspace not found")
    if body.amount == 0:
        raise HTTPException(422, "Amount must not be zero")
    if ws.credits_balance + body.amount < 0:
        raise HTTPException(422, "Balance cannot go below zero")
    action = LedgerAction.grant if body.amount > 0 else LedgerAction.adjustment
    await credits.grant(db, ws.id, body.amount, note=body.note, action=action, expires_in_days=body.expires_in_days)
    await db.commit()
    await db.refresh(ws)
    return ws


@admin.post("/expire-credits", response_model=ExpiryOut)
async def expire_credits(db: DB):
    """Run credit expiry now (the worker also runs it every hour)."""
    res = await credits.expire_due_credits(db)
    return ExpiryOut(expired_grants=res.expired_grants, credits_expired=res.credits_expired)


router.include_router(admin)
