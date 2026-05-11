from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from server.api.deps import DB, AdminUser
from server.models.audit_log import AuditLog

router = APIRouter(prefix="/api/audit-logs", tags=["audit-logs"])


class AuditLogResponse(BaseModel):
    id: uuid.UUID
    site_id: uuid.UUID | None
    user_id: uuid.UUID
    user_name: str | None = None
    user_email: str | None = None
    action: str
    details: dict
    created_at: datetime

    class Config:
        from_attributes = True


@router.get("", response_model=list[AuditLogResponse])
async def list_audit_logs(
    db: DB,
    admin: AdminUser,
    site_id: uuid.UUID | None = Query(None),
    action: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    stmt = select(AuditLog).options(joinedload(AuditLog.user)).order_by(AuditLog.created_at.desc())

    if site_id is not None:
        stmt = stmt.where(AuditLog.site_id == site_id)
    if action is not None:
        stmt = stmt.where(AuditLog.action == action)

    stmt = stmt.offset(offset).limit(limit)
    result = await db.execute(stmt)
    logs = list(result.unique().scalars().all())

    return [
        AuditLogResponse(
            id=log.id,
            site_id=log.site_id,
            user_id=log.user_id,
            user_name=log.user.name if log.user else None,
            user_email=log.user.email if log.user else None,
            action=log.action,
            details=log.details,
            created_at=log.created_at,
        )
        for log in logs
    ]
