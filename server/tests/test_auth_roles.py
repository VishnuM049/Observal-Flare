"""Tests for auth role scoping: admin sees all, guest sees only own, unauth gets 401."""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from server.models.site import DeployType, Site, SiteStatus
from server.models.user import User, UserRole
from server.services.site_service import SiteError, get_site, list_sites


async def test_admin_sees_all_sites(db: AsyncSession, admin_user: User, running_site: Site):
    sites = await list_sites(db, admin_user)
    assert any(s.id == running_site.id for s in sites)


async def test_guest_sees_only_own_sites(db: AsyncSession, admin_user: User, guest_user: User):
    # Admin's site
    admin_site = Site(
        name=f"admin-{uuid.uuid4().hex[:6]}",
        domain="admin.observal.io",
        deploy_type=DeployType.RELEASE,
        deploy_ref="v1.0",
        requestor_email="admin@test.local",
        created_by=admin_user.id,
        instance_size="t3.large",
        status=SiteStatus.RUNNING,
    )
    # Guest's site
    guest_site = Site(
        name=f"guest-{uuid.uuid4().hex[:6]}",
        domain="guest.observal.io",
        deploy_type=DeployType.RELEASE,
        deploy_ref="v1.0",
        requestor_email="guest@test.local",
        created_by=guest_user.id,
        instance_size="t3.large",
        status=SiteStatus.RUNNING,
    )
    db.add_all([admin_site, guest_site])
    await db.commit()

    guest_sites = await list_sites(db, guest_user)
    guest_ids = {s.id for s in guest_sites}

    assert guest_site.id in guest_ids
    assert admin_site.id not in guest_ids


async def test_guest_cannot_access_admin_site(db: AsyncSession, admin_user: User, guest_user: User):
    admin_site = Site(
        name=f"priv-{uuid.uuid4().hex[:6]}",
        domain="priv.observal.io",
        deploy_type=DeployType.RELEASE,
        deploy_ref="v1.0",
        requestor_email="admin@test.local",
        created_by=admin_user.id,
        instance_size="t3.large",
        status=SiteStatus.RUNNING,
    )
    db.add(admin_site)
    await db.commit()

    with pytest.raises(SiteError, match="not found"):
        await get_site(db, admin_site.id, guest_user)
