"""ARQ worker entrypoint:  arq app.workers.arq_worker.WorkerSettings

ARQ (asyncio + Redis) is used instead of Celery because the form engine, the database layer and
the LLM client are all async; one worker process drives `WORKER_CONCURRENCY` browser contexts
from a single shared Chromium.
"""

import logging

from arq import cron

from app.core.config import get_settings
from app.core.database import get_engine, init_engine, session_factory
from app.services.credits import expire_due_credits
from app.workers.form_probe import FormEngine, RobotsChecker
from app.workers.queue import redis_settings
from app.workers.submission_worker import process_submission
from app.workers.triage_worker import triage_reply

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


async def startup(ctx: dict) -> None:
    settings = get_settings()
    settings.screenshot_dir.mkdir(parents=True, exist_ok=True)
    init_engine()
    ctx["engine"] = await FormEngine().start()
    ctx["robots"] = RobotsChecker(settings.bot_user_agent_token)


async def shutdown(ctx: dict) -> None:
    await ctx["engine"].close()
    await ctx["robots"].aclose()
    await get_engine().dispose()


async def expire_credits(ctx: dict) -> dict:
    """Hourly: expire unused credits past their grant's expires_at."""
    async with session_factory()() as session:
        res = await expire_due_credits(session)
    return {"expired_grants": res.expired_grants, "credits_expired": res.credits_expired}


class WorkerSettings:
    functions = [process_submission, triage_reply, expire_credits]
    cron_jobs = [cron(expire_credits, minute=7, run_at_startup=True, unique=True)]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = redis_settings()
    max_jobs = get_settings().worker_concurrency
    job_timeout = 240
    # One try only: retrying a job that crashed after clicking submit could message a company twice.
    max_tries = 1
    keep_result = 3600
