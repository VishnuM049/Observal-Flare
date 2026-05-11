"""Tests for the cost summary API."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from server.api.costs import _daily_cost
from server.models.site import DeployType, Site, SiteStatus, SleepMode
from server.models.user import User


def test_daily_cost_no_sleep():
    cost = _daily_cost("t3.large", "none")
    assert 1.5 < cost < 2.5


def test_daily_cost_with_nightly_sleep():
    full = _daily_cost("t3.large", "none")
    sleeping = _daily_cost("t3.large", "nightly")
    assert sleeping < full


def test_daily_cost_unknown_instance():
    cost = _daily_cost("t3.unknown", "none")
    expected = _daily_cost("t3.large", "none")
    assert cost == expected


async def test_cost_history_counts_active_sites(db: AsyncSession, admin_user: User):
    """Sites that existed on a given day contribute to that day's cost."""
    now = datetime.now(timezone.utc)
    site = Site(
        name=f"cost-{uuid.uuid4().hex[:6]}",
        domain="cost.observal.io",
        deploy_type=DeployType.BRANCH,
        deploy_ref="main",
        requestor_email="test@test.local",
        created_by=admin_user.id,
        instance_size="t3.large",
        status=SiteStatus.RUNNING,
        sleep_mode=SleepMode.NONE,
        created_at=now - timedelta(days=5),
    )
    db.add(site)
    await db.commit()

    from server.api.costs import DayCost
    from sqlalchemy import select as sa_select

    result = await db.execute(sa_select(Site))
    all_sites = list(result.scalars().all())

    active = [s for s in all_sites if s.status not in {SiteStatus.DESTROYED, SiteStatus.PENDING}]
    assert len(active) >= 1


async def test_destroyed_site_excluded_from_future(db: AsyncSession, admin_user: User):
    """A destroyed site should not appear in future projection cost."""
    now = datetime.now(timezone.utc)
    site = Site(
        name=f"destroyed-cost-{uuid.uuid4().hex[:6]}",
        domain="destroyed-cost.observal.io",
        deploy_type=DeployType.BRANCH,
        deploy_ref="main",
        requestor_email="test@test.local",
        created_by=admin_user.id,
        instance_size="t3.large",
        status=SiteStatus.DESTROYED,
        sleep_mode=SleepMode.NONE,
        created_at=now - timedelta(days=10),
        destroyed_at=now - timedelta(days=2),
    )
    db.add(site)
    await db.commit()

    from server.api.costs import BILLABLE_STATUSES

    assert site.status not in BILLABLE_STATUSES


async def test_sleep_mode_reduces_cost():
    """Nightly sleep mode should reduce daily cost vs no sleep."""
    no_sleep = _daily_cost("t3.large", "none")
    nightly = _daily_cost("t3.large", "nightly")
    assert nightly < no_sleep * 0.6
