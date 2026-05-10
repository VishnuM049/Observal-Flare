from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from server.api.deps import DB, AdminUser
from server.models.invite import Invite
from server.models.site import Site
from server.models.user import User
from server.services.invite_service import create_invite

from sqlalchemy import select

router = APIRouter(prefix="/api/invites", tags=["invites"])


class InviteCreateRequest(BaseModel):
    label: str | None = None
    max_sites: int = 1
    allowed_instance_sizes: list[str] = ["t3.large"]
    forced_ttl_days: int | None = 7
    allowed_deploy_types: list[str] = ["release", "tag"]
    env_overrides_locked: bool = True
    expires_at: datetime
    max_uses: int | None = None


class InviteResponse(BaseModel):
    id: uuid.UUID
    token: str
    label: str | None
    max_sites: int
    allowed_instance_sizes: list
    forced_ttl_days: int | None
    allowed_deploy_types: list
    env_overrides_locked: bool
    expires_at: datetime
    max_uses: int | None
    use_count: int
    created_at: datetime

    class Config:
        from_attributes = True


class InviteUsageResponse(BaseModel):
    invite: InviteResponse
    users: list[dict]
    sites: list[dict]


@router.get("", response_model=list[InviteResponse])
async def list_invites(db: DB, admin: AdminUser):
    result = await db.execute(select(Invite).order_by(Invite.created_at.desc()))
    return [InviteResponse.model_validate(i) for i in result.scalars().all()]


@router.post("", response_model=InviteResponse, status_code=201)
async def create_new_invite(body: InviteCreateRequest, db: DB, admin: AdminUser):
    invite = await create_invite(
        db,
        created_by=admin.id,
        label=body.label,
        max_sites=body.max_sites,
        allowed_instance_sizes=body.allowed_instance_sizes,
        forced_ttl_days=body.forced_ttl_days,
        allowed_deploy_types=body.allowed_deploy_types,
        env_overrides_locked=body.env_overrides_locked,
        expires_at=body.expires_at,
        max_uses=body.max_uses,
    )
    return InviteResponse.model_validate(invite)


@router.delete("/{invite_id}", status_code=204)
async def revoke_invite(invite_id: uuid.UUID, db: DB, admin: AdminUser):
    invite = await db.get(Invite, invite_id)
    if invite is None:
        raise HTTPException(status_code=404, detail="Invite not found")
    await db.delete(invite)
    await db.commit()


@router.get("/{invite_id}/usage", response_model=InviteUsageResponse)
async def get_invite_usage(invite_id: uuid.UUID, db: DB, admin: AdminUser):
    invite = await db.get(Invite, invite_id)
    if invite is None:
        raise HTTPException(status_code=404, detail="Invite not found")

    users_result = await db.execute(select(User).where(User.invite_id == invite_id))
    users = [{"id": str(u.id), "name": u.name, "email": u.email, "created_at": u.created_at.isoformat()} for u in users_result.scalars().all()]

    sites_result = await db.execute(select(Site).where(Site.invite_id == invite_id))
    sites = [{"id": str(s.id), "name": s.name, "status": s.status.value, "created_at": s.created_at.isoformat()} for s in sites_result.scalars().all()]

    return InviteUsageResponse(
        invite=InviteResponse.model_validate(invite),
        users=users,
        sites=sites,
    )
