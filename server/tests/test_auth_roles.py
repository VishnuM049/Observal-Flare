"""Tests for auth role scoping: admin sees all sites, member sees all sites."""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from server.models.site import DeployType, Site, SiteStatus
from server.models.user import User, UserRole
from server.services.site_service import get_site, list_sites


async def test_admin_sees_all_sites(db: AsyncSession, admin_user: User, running_site: Site):
    sites = await list_sites(db, admin_user)
    assert any(s.id == running_site.id for s in sites)


async def test_member_sees_all_sites(db: AsyncSession, admin_user: User):
    member = User(email=f"member-{uuid.uuid4().hex[:6]}@test.local", name="Test Member", role=UserRole.MEMBER)
    db.add(member)
    await db.commit()
    await db.refresh(member)

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
    db.add(admin_site)
    await db.commit()

    sites = await list_sites(db, member)
    assert any(s.id == admin_site.id for s in sites)
