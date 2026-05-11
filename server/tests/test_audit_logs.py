"""Tests for the audit log API endpoint."""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from server.models.audit_log import AuditLog
from server.models.site import DeployType, Site, SiteStatus
from server.models.user import User, UserRole


async def test_list_audit_logs(db: AsyncSession, admin_user: User):
    """Admin can list audit logs."""
    site = Site(
        name=f"audit-{uuid.uuid4().hex[:6]}",
        domain="audit.observal.io",
        deploy_type=DeployType.BRANCH,
        deploy_ref="main",
        requestor_email="test@test.local",
        created_by=admin_user.id,
        instance_size="t3.large",
        status=SiteStatus.RUNNING,
    )
    db.add(site)
    await db.flush()

    log1 = AuditLog(user_id=admin_user.id, site_id=site.id, action="site.created", details={"name": site.name})
    log2 = AuditLog(user_id=admin_user.id, site_id=site.id, action="site.destroyed", details={})
    db.add_all([log1, log2])
    await db.commit()

    from server.api.audit_logs import AuditLogResponse

    stmt = (
        __import__("sqlalchemy", fromlist=["select"]).select(AuditLog)
        .order_by(AuditLog.created_at.desc())
    )
    result = await db.execute(stmt)
    logs = list(result.scalars().all())

    assert len(logs) == 2
    assert {l.action for l in logs} == {"site.created", "site.destroyed"}


async def test_filter_by_action(db: AsyncSession, admin_user: User):
    """Filtering by action returns only matching logs."""
    log1 = AuditLog(user_id=admin_user.id, action="site.created", details={})
    log2 = AuditLog(user_id=admin_user.id, action="site.destroyed", details={})
    log3 = AuditLog(user_id=admin_user.id, action="site.created", details={})
    db.add_all([log1, log2, log3])
    await db.commit()

    from sqlalchemy import select

    stmt = select(AuditLog).where(AuditLog.action == "site.created")
    result = await db.execute(stmt)
    logs = list(result.scalars().all())

    assert len(logs) == 2
    assert all(l.action == "site.created" for l in logs)


async def test_filter_by_site_id(db: AsyncSession, admin_user: User):
    """Filtering by site_id returns only logs for that site."""
    site = Site(
        name=f"audit-filter-{uuid.uuid4().hex[:6]}",
        domain="audit-filter.observal.io",
        deploy_type=DeployType.BRANCH,
        deploy_ref="main",
        requestor_email="test@test.local",
        created_by=admin_user.id,
        instance_size="t3.large",
        status=SiteStatus.RUNNING,
    )
    db.add(site)
    await db.flush()

    log1 = AuditLog(user_id=admin_user.id, site_id=site.id, action="site.created", details={})
    log2 = AuditLog(user_id=admin_user.id, site_id=None, action="site.created", details={})
    db.add_all([log1, log2])
    await db.commit()

    from sqlalchemy import select

    stmt = select(AuditLog).where(AuditLog.site_id == site.id)
    result = await db.execute(stmt)
    logs = list(result.scalars().all())

    assert len(logs) == 1
    assert logs[0].site_id == site.id


async def test_audit_log_user_association(db: AsyncSession, admin_user: User):
    """Audit log entries correctly reference the user who performed the action."""
    log = AuditLog(user_id=admin_user.id, action="site.created", details={"test": True})
    db.add(log)
    await db.commit()
    await db.refresh(log)

    user = await db.get(User, log.user_id)
    assert user is not None
    assert user.id == admin_user.id
    assert user.name == admin_user.name


async def test_pagination(db: AsyncSession, admin_user: User):
    """Limit and offset work correctly."""
    for i in range(5):
        db.add(AuditLog(user_id=admin_user.id, action=f"test.action{i}", details={}))
    await db.commit()

    from sqlalchemy import select

    stmt = select(AuditLog).order_by(AuditLog.created_at.desc()).limit(2).offset(0)
    result = await db.execute(stmt)
    page1 = list(result.scalars().all())
    assert len(page1) == 2

    stmt2 = select(AuditLog).order_by(AuditLog.created_at.desc()).limit(2).offset(2)
    result2 = await db.execute(stmt2)
    page2 = list(result2.scalars().all())
    assert len(page2) == 2

    assert {l.id for l in page1}.isdisjoint({l.id for l in page2})
