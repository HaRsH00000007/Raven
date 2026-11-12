"""Thin wrapper around the ARQ (Redis) job queue."""

import logging
import uuid

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from app.core.config import get_settings

log = logging.getLogger("raven.queue")

_pool: ArqRedis | None = None


def redis_settings() -> RedisSettings:
    return RedisSettings.from_dsn(get_settings().redis_url)


async def get_pool() -> ArqRedis:
    global _pool
    if _pool is None:
        _pool = await create_pool(redis_settings())
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.aclose()
        _pool = None


async def enqueue_submissions(ids: list[uuid.UUID]) -> int:
    pool = await get_pool()
    n = 0
    for sid in ids:
        # _job_id dedupes: a submission is never queued twice concurrently.
        job = await pool.enqueue_job("process_submission", str(sid), _job_id=f"submission:{sid}")
        if job is not None:
            n += 1
    return n


async def enqueue_reply(reply_id: uuid.UUID) -> None:
    pool = await get_pool()
    await pool.enqueue_job("triage_reply", str(reply_id), _job_id=f"reply:{reply_id}")
