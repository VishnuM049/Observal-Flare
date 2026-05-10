from __future__ import annotations

import secrets
import uuid
from datetime import datetime

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from server.models.invite import Invite
from server.models.site import Site
from server.models.user import User, UserRole


class InviteError(Exception):
    pass


async def create_invite(
    db: AsyncSession,
    *,
    created_by: uuid.UUID,
    label: str | None,
    max_sites: int,
    allowed_instance_sizes: list[str],
    forced_ttl_days: int | None,
    allowed_deploy_types: list[str],
    env_overrides_locked: bool,
    expires_at: datetime,
    max_uses: int | None,
) -> Invite:
    invite = Invite(
        token=secrets.token_urlsafe(16)[:12],
        created_by=created_by,
        label=label,
        max_sites=max_sites,
        allowed_instance_sizes=allowed_instance_sizes,
        forced_ttl_days=forced_ttl_days,
        allowed_deploy_types=allowed_deploy_types,
        env_overrides_locked=env_overrides_locked,
        expires_at=expires_at,
        max_uses=max_uses,
    )
    db.add(invite)
    await db.commit()
    await db.refresh(invite)
    return invite


async def validate_invite_token(db: AsyncSession, token: str) -> Invite:
    result = await db.execute(select(Invite).where(Invite.token == token))
    invite = result.scalar_one_or_none()
    if invite is None:
        raise InviteError("Invalid invite link")
    if invite.expires_at < datetime.now(invite.expires_at.tzinfo):
        raise InviteError("This invite has expired")
    if invite.max_uses is not None and invite.use_count >= invite.max_uses:
        raise InviteError("This invite has reached its usage limit")
    return invite


async def redeem_invite(db: AsyncSession, invite: Invite, *, name: str, email: str) -> User:
    existing = await db.execute(select(User).where(User.email == email))
    if existing.scalar_one_or_none() is not None:
        raise InviteError("An account with this email already exists")

    user = User(
        name=name,
        email=email,
        role=UserRole.GUEST,
        invite_id=invite.id,
    )
    invite.use_count += 1
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def check_guest_site_limit(db: AsyncSession, user: User) -> None:
    if user.role != UserRole.GUEST or user.invite_id is None:
        return
    invite = await db.get(Invite, user.invite_id)
    if invite is None:
        raise InviteError("Invite not found")

    count_result = await db.execute(
        select(func.count()).select_from(Site).where(
            Site.invite_id == invite.id,
            Site.created_by == user.id,
            Site.status.notin_(["destroyed"]),
        )
    )
    current_count = count_result.scalar_one()
    if current_count >= invite.max_sites:
        raise InviteError(f"You've reached your site limit ({invite.max_sites})")
