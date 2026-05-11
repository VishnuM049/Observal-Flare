"""Tests for cron jobs: nightly sleep, destroy expired, stale reminders."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.models.site import DeployType, Site, SiteStatus, SleepMode
from server.models.user import User, UserRole
from server.worker.tasks import cron_destroy_expired, cron_nightly_sleep, cron_stale_reminders, task_sleep_site


def _patch_session(db: AsyncSession):
    """Patch async_session to return the test session instead of creating a new one."""
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _fake_session():
        yield db

    return patch("server.worker.tasks.async_session", _fake_session)


async def test_nightly_sleep_only_affects_nightly_running(db: AsyncSession):
    """Only running sites with sleep_mode=nightly get stopped."""
    user = User(email=f"cron-{uuid.uuid4().hex[:6]}@test.local", name="Cron Test", role=UserRole.ADMIN)
    db.add(user)
    await db.commit()
    await db.refresh(user)

    nightly_running = Site(
        name=f"nightly-run-{uuid.uuid4().hex[:6]}", domain="a.test", deploy_type=DeployType.RELEASE,
        deploy_ref="v1", requestor_email="t@example.com", created_by=user.id, instance_size="t3.large",
        status=SiteStatus.RUNNING, sleep_mode=SleepMode.NIGHTLY, instance_id="i-mock-1",
    )
    idle_running = Site(
        name=f"idle-run-{uuid.uuid4().hex[:6]}", domain="b.test", deploy_type=DeployType.BRANCH,
        deploy_ref="main", requestor_email="t@example.com", created_by=user.id, instance_size="t3.large",
        status=SiteStatus.RUNNING, sleep_mode=SleepMode.IDLE, instance_id="i-mock-2",
    )
    nightly_stopped = Site(
        name=f"nightly-stop-{uuid.uuid4().hex[:6]}", domain="c.test", deploy_type=DeployType.RELEASE,
        deploy_ref="v1", requestor_email="t@example.com", created_by=user.id, instance_size="t3.large",
        status=SiteStatus.STOPPED, sleep_mode=SleepMode.NIGHTLY, instance_id="i-mock-3",
    )
    db.add_all([nightly_running, idle_running, nightly_stopped])
    await db.commit()

    with _patch_session(db):
        await cron_nightly_sleep({})

    await db.refresh(nightly_running)
    await db.refresh(idle_running)
    await db.refresh(nightly_stopped)

    assert nightly_running.status == SiteStatus.SLEEPING
    assert idle_running.status == SiteStatus.RUNNING
    assert nightly_stopped.status == SiteStatus.STOPPED


async def test_destroy_expired_sites(db: AsyncSession):
    """Sites past scheduled_destroy_at get destroyed."""
    user = User(email=f"cron-{uuid.uuid4().hex[:6]}@test.local", name="Cron Test", role=UserRole.ADMIN)
    db.add(user)
    await db.commit()
    await db.refresh(user)

    expired = Site(
        name=f"expired-{uuid.uuid4().hex[:6]}", domain="exp.test", deploy_type=DeployType.PR,
        deploy_ref="42", requestor_email="t@example.com", created_by=user.id, instance_size="t3.large",
        status=SiteStatus.RUNNING, instance_id="i-mock-exp", ip_address="127.0.0.1",
        scheduled_destroy_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    not_expired = Site(
        name=f"future-{uuid.uuid4().hex[:6]}", domain="fut.test", deploy_type=DeployType.PR,
        deploy_ref="43", requestor_email="t@example.com", created_by=user.id, instance_size="t3.large",
        status=SiteStatus.RUNNING, instance_id="i-mock-fut", ip_address="127.0.0.1",
        scheduled_destroy_at=datetime.now(timezone.utc) + timedelta(hours=23),
    )
    db.add_all([expired, not_expired])
    await db.commit()

    with _patch_session(db):
        await cron_destroy_expired({})

    await db.refresh(expired)
    await db.refresh(not_expired)

    assert expired.status == SiteStatus.DESTROYED
    assert not_expired.status == SiteStatus.RUNNING


async def test_stale_reminders(db: AsyncSession):
    """Sites past their TTL get a reminder (reminder_sent_at set)."""
    user = User(email=f"cron-{uuid.uuid4().hex[:6]}@test.local", name="Cron Test", role=UserRole.ADMIN)
    db.add(user)
    await db.commit()
    await db.refresh(user)

    stale = Site(
        name=f"stale-{uuid.uuid4().hex[:6]}", domain="stale.test", deploy_type=DeployType.BRANCH,
        deploy_ref="main", requestor_email="t@example.com", created_by=user.id, instance_size="t3.large",
        status=SiteStatus.RUNNING, ttl_days=1,
        created_at=datetime.now(timezone.utc) - timedelta(days=3),
    )
    fresh = Site(
        name=f"fresh-{uuid.uuid4().hex[:6]}", domain="fresh.test", deploy_type=DeployType.BRANCH,
        deploy_ref="main", requestor_email="t@example.com", created_by=user.id, instance_size="t3.large",
        status=SiteStatus.RUNNING, ttl_days=7,
        created_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    db.add_all([stale, fresh])
    await db.commit()

    with _patch_session(db):
        await cron_stale_reminders({})

    await db.refresh(stale)
    await db.refresh(fresh)

    assert stale.reminder_sent_at is not None
    assert fresh.reminder_sent_at is None


async def test_sleep_site_transitions_to_sleeping(db: AsyncSession):
    """task_sleep_site should set status to SLEEPING, not STOPPED."""
    user = User(email=f"cron-{uuid.uuid4().hex[:6]}@test.local", name="Cron Test", role=UserRole.ADMIN)
    db.add(user)
    await db.commit()
    await db.refresh(user)

    site = Site(
        name=f"idle-{uuid.uuid4().hex[:6]}", domain="idle.test", deploy_type=DeployType.BRANCH,
        deploy_ref="main", requestor_email="t@example.com", created_by=user.id, instance_size="t3.large",
        status=SiteStatus.RUNNING, sleep_mode=SleepMode.IDLE, instance_id="i-mock-idle",
    )
    db.add(site)
    await db.commit()
    await db.refresh(site)

    with _patch_session(db):
        await task_sleep_site({}, str(site.id))

    await db.refresh(site)
    assert site.status == SiteStatus.SLEEPING


async def test_sleep_site_skips_non_running(db: AsyncSession):
    """task_sleep_site should not touch sites that aren't running."""
    user = User(email=f"cron-{uuid.uuid4().hex[:6]}@test.local", name="Cron Test", role=UserRole.ADMIN)
    db.add(user)
    await db.commit()
    await db.refresh(user)

    site = Site(
        name=f"stopped-{uuid.uuid4().hex[:6]}", domain="stopped.test", deploy_type=DeployType.BRANCH,
        deploy_ref="main", requestor_email="t@example.com", created_by=user.id, instance_size="t3.large",
        status=SiteStatus.STOPPED, sleep_mode=SleepMode.IDLE, instance_id="i-mock-stopped",
    )
    db.add(site)
    await db.commit()
    await db.refresh(site)

    with _patch_session(db):
        await task_sleep_site({}, str(site.id))

    await db.refresh(site)
    assert site.status == SiteStatus.STOPPED
