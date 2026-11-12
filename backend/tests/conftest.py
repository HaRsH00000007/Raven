import os
import tempfile
from pathlib import Path

# Configure settings before any app module reads them.
_TMP = Path(tempfile.mkdtemp(prefix="raven-test-"))
os.environ.update(
    {
        "DATABASE_URL": f"sqlite+aiosqlite:///{(_TMP / 'test.db').as_posix()}",
        "ADMIN_TOKEN": "test-admin",
        "INBOUND_WEBHOOK_TOKEN": "test-hook",
        "REPLY_DOMAIN": "replies.test.dev",
        "ANTHROPIC_API_KEY": "",
        "SMTP_HOST": "",
        "TWILIO_AUTH_TOKEN": "twilio-secret",
        "PUBLIC_BASE_URL": "https://api.test.dev",
        "SCREENSHOT_DIR": str(_TMP / "shots"),
        "DEV_BOOTSTRAP_API_KEY": "",
    }
)

import httpx  # noqa: E402
import pytest  # noqa: E402

from app.core.config import get_settings  # noqa: E402

get_settings.cache_clear()

from app.core import database  # noqa: E402
from app.core.database import Base  # noqa: E402


@pytest.fixture
async def db_engine():
    # Set TEST_DATABASE_URL (postgresql+asyncpg://...) to run the suite against Postgres.
    url = os.environ.get("TEST_DATABASE_URL") or f"sqlite+aiosqlite:///{(_TMP / f'db-{os.urandom(4).hex()}.db').as_posix()}"
    engine = database.init_engine(url)
    async with engine.begin() as conn:
        if url.startswith("postgresql"):
            await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
def queued(monkeypatch):
    """Capture enqueue calls instead of talking to Redis. Replies fail to enqueue so the
    webhook falls back to inline triage (exercising that path)."""
    from app.workers import queue

    calls = {"submissions": [], "replies": []}

    async def enqueue_submissions(ids):
        calls["submissions"].extend(ids)
        return len(ids)

    async def enqueue_reply(rid):
        calls["replies"].append(rid)
        raise ConnectionError("redis down (test)")

    monkeypatch.setattr(queue, "enqueue_submissions", enqueue_submissions)
    monkeypatch.setattr(queue, "enqueue_reply", enqueue_reply)
    return calls


@pytest.fixture
async def client(db_engine, queued):
    from app.main import create_app

    app = create_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.fixture
async def api(client):
    r = await client.post(
        "/api/v1/admin/workspaces",
        json={"name": "Glocal RPO", "credits": 10},
        headers={"X-Admin-Token": "test-admin"},
    )
    assert r.status_code == 201, r.text
    client.headers["X-API-Key"] = r.json()["api_key"]
    client.workspace_id = r.json()["id"]
    return client
