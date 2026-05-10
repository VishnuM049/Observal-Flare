"""Tests for invite limits and expiry."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from server.models.invite import Invite
from server.models.site import DeployType, SiteStatus
from server.models.user import User, UserRole
from server.services.invite_service import InviteError, validate_invite_token
from server.services.site_service import SiteError, create_site


async def test_invite_limits_deploy_type(db: AsyncSession, guest_user: User, invite: Invite):
    """Guest can't use deploy types not in invite.allowed_deploy_types."""
    with pytest.raises(InviteError, match="Deploy type"):
        await create_site(
            db,
            user=guest_user,
            name=f"bad-{uuid.uuid4().hex[:6]}",
            deploy_type=DeployType.BRANCH,
            deploy_ref="main",
            requestor_email="guest@test.local",
        )


async def test_invite_limits_instance_size(db: AsyncSession, guest_user: User, invite: Invite):
    """Guest can't use instance sizes not in invite.allowed_instance_sizes."""
    with pytest.raises(InviteError, match="Instance size"):
        await create_site(
            db,
            user=guest_user,
            name=f"bad-{uuid.uuid4().hex[:6]}",
            deploy_type=DeployType.RELEASE,
            deploy_ref="v0.4.0",
            requestor_email="guest@test.local",
            instance_size="t3.xlarge",
        )


async def test_invite_limits_max_sites(db: AsyncSession, guest_user: User, invite: Invite):
    """Guest can't exceed invite.max_sites."""
    # invite.max_sites = 2, create 2 sites
    for i in range(2):
        await create_site(
            db,
            user=guest_user,
            name=f"ok-{uuid.uuid4().hex[:6]}",
            deploy_type=DeployType.RELEASE,
            deploy_ref="v0.4.0",
            requestor_email="guest@test.local",
        )

    with pytest.raises(InviteError, match="site limit"):
        await create_site(
            db,
            user=guest_user,
            name=f"over-{uuid.uuid4().hex[:6]}",
            deploy_type=DeployType.RELEASE,
            deploy_ref="v0.4.0",
            requestor_email="guest@test.local",
        )


async def test_invite_limits_env_overrides_locked(db: AsyncSession, guest_user: User, invite: Invite):
    """Guest can't set env overrides when invite.env_overrides_locked=True."""
    with pytest.raises(InviteError, match="overrides are locked"):
        await create_site(
            db,
            user=guest_user,
            name=f"env-{uuid.uuid4().hex[:6]}",
            deploy_type=DeployType.RELEASE,
            deploy_ref="v0.4.0",
            requestor_email="guest@test.local",
            env_overrides={"SECRET": "leaked"},
        )


async def test_invite_expiry(db: AsyncSession, admin_user: User):
    """Expired invite token is rejected."""
    expired = Invite(
        token=uuid.uuid4().hex[:12],
        created_by=admin_user.id,
        expires_at=datetime.now(timezone.utc) - timedelta(days=1),
    )
    db.add(expired)
    await db.commit()

    with pytest.raises(InviteError, match="expired"):
        await validate_invite_token(db, expired.token)


async def test_invite_max_uses(db: AsyncSession, admin_user: User):
    """Invite at max uses is rejected."""
    used_up = Invite(
        token=uuid.uuid4().hex[:12],
        created_by=admin_user.id,
        expires_at=datetime.now(timezone.utc) + timedelta(days=30),
        max_uses=1,
        use_count=1,
    )
    db.add(used_up)
    await db.commit()

    with pytest.raises(InviteError, match="usage limit"):
        await validate_invite_token(db, used_up.token)
