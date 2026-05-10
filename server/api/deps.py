"""Shared FastAPI dependencies: DB session, current user, role checks."""
from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Cookie, Depends, HTTPException, status
from itsdangerous import BadSignature, URLSafeTimedSerializer
from sqlalchemy.ext.asyncio import AsyncSession

from server.config import get_settings
from server.database import get_db
from server.models.user import User, UserRole

DB = Annotated[AsyncSession, Depends(get_db)]


def _get_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().secret_key)


async def get_current_user(
    db: DB,
    session_token: str | None = Cookie(None),
) -> User:
    if not session_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    serializer = _get_serializer()
    try:
        user_id = serializer.loads(session_token, max_age=86400 * 7)
    except BadSignature:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session")
    user = await db.get(User, uuid.UUID(user_id))
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive")
    return user


def create_session_token(user_id: uuid.UUID) -> str:
    serializer = _get_serializer()
    return serializer.dumps(str(user_id))


CurrentUser = Annotated[User, Depends(get_current_user)]


async def require_admin(user: CurrentUser) -> User:
    if user.role != UserRole.ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return user


AdminUser = Annotated[User, Depends(require_admin)]


async def require_internal(user: CurrentUser) -> User:
    if user.role == UserRole.GUEST:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Internal access required")
    return user
