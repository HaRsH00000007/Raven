from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.models import Workspace
from app.services.security import constant_time_equals, hash_api_key

DB = Annotated[AsyncSession, Depends(get_db)]


async def current_workspace(
    db: DB, x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None
) -> Workspace:
    if not x_api_key:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing X-API-Key header")
    ws = await db.scalar(select(Workspace).where(Workspace.api_key_hash == hash_api_key(x_api_key)))
    if ws is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid API key")
    return ws


async def require_admin(x_admin_token: Annotated[str | None, Header(alias="X-Admin-Token")] = None) -> None:
    expected = get_settings().admin_token
    if not x_admin_token or not constant_time_equals(x_admin_token, expected):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid admin token")


CurrentWorkspace = Annotated[Workspace, Depends(current_workspace)]
