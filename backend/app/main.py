import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from app.api.router import api_router
from app.core.config import get_settings
from app.core.database import get_engine, session_factory
from app.models import Workspace
from app.services import credits
from app.services.security import hash_api_key
from app.workers.queue import close_pool

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("raven")


async def bootstrap_dev_workspace() -> None:
    """Create a workspace for DEV_BOOTSTRAP_API_KEY so docker-compose works out of the box."""
    s = get_settings()
    if not s.dev_bootstrap_api_key:
        return
    async with session_factory()() as session:
        h = hash_api_key(s.dev_bootstrap_api_key)
        if await session.scalar(select(Workspace.id).where(Workspace.api_key_hash == h)):
            return
        ws = Workspace(
            name=s.dev_bootstrap_workspace_name,
            api_key_hash=h,
            price_per_form_cents=s.default_price_per_form_cents,
            forward_emails=[],
        )
        session.add(ws)
        await session.flush()
        if s.dev_bootstrap_credits:
            await credits.grant(session, ws.id, s.dev_bootstrap_credits, note="Dev bootstrap grant")
        await session.commit()
        log.info("Bootstrapped workspace '%s' for DEV_BOOTSTRAP_API_KEY", ws.name)


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    s.screenshot_dir.mkdir(parents=True, exist_ok=True)
    await bootstrap_dev_workspace()
    yield
    await close_pool()
    await get_engine().dispose()


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(title="Raven API", version="1.0.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=s.cors_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(api_router)

    @app.get("/healthz", tags=["health"])
    async def healthz():
        return {"status": "ok"}

    return app


app = create_app()
