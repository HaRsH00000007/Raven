from fastapi import APIRouter

from app.api.v1 import campaigns, simulate, submissions, triage, webhooks, workspace

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(workspace.router)
api_router.include_router(campaigns.router)
api_router.include_router(submissions.router)
api_router.include_router(triage.router)
api_router.include_router(webhooks.router)
api_router.include_router(simulate.router)
