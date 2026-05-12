"""Tests for the cost summary API."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from sqlalchemy.ext.asyncio import AsyncSession

from server.api.costs import _daily_cost_for_site, _hourly_rate, _running_fraction
from server.models.site import DeployType, Site, SiteStatus, SleepMode
from server.models.user import User


def _mock_site(**kwargs) -> Site:
    defaults = dict(
        instance_size="t3.large",
        sleep_mode=SleepMode.NONE,
        sleep_at_hour=19,
        wake_at_hour=7,
        idle_timeout_minutes=120,
    )
    defaults.update(kwargs)
    site = MagicMock(spec=Site)
    for k, v in defaults.items():
        setattr(site, k, v)
    return site


def test_daily_cost_no_sleep():
    site = _mock_site(sleep_mode=SleepMode.NONE)
    cost = _daily_cost_for_site(site)
    assert 1.5 < cost < 2.5


def test_daily_cost_with_nightly_sleep():
    full = _daily_cost_for_site(_mock_site(sleep_mode=SleepMode.NONE))
    sleeping = _daily_cost_for_site(_mock_site(sleep_mode=SleepMode.NIGHTLY))
    assert sleeping < full


def test_daily_cost_unknown_instance():
    cost = _daily_cost_for_site(_mock_site(instance_size="t3.unknown"))
    expected = _daily_cost_for_site(_mock_site(instance_size="t3.large"))
    assert cost == expected


def test_nightly_fraction_default():
    site = _mock_site(sleep_mode=SleepMode.NIGHTLY, wake_at_hour=7, sleep_at_hour=19)
    assert abs(_running_fraction(site) - 12 / 24) < 0.01


def test_nightly_fraction_short_day():
    site = _mock_site(sleep_mode=SleepMode.NIGHTLY, wake_at_hour=7, sleep_at_hour=13)
    assert abs(_running_fraction(site) - 6 / 24) < 0.01


def test_nightly_fraction_overnight():
    site = _mock_site(sleep_mode=SleepMode.NIGHTLY, wake_at_hour=22, sleep_at_hour=6)
    assert abs(_running_fraction(site) - 8 / 24) < 0.01


def test_short_nightly_reduces_cost():
    full_day = _daily_cost_for_site(_mock_site(sleep_mode=SleepMode.NIGHTLY, wake_at_hour=7, sleep_at_hour=19))
    half_day = _daily_cost_for_site(_mock_site(sleep_mode=SleepMode.NIGHTLY, wake_at_hour=7, sleep_at_hour=13))
    assert half_day < full_day * 0.7


async def test_cost_history_counts_active_sites(db: AsyncSession, admin_user: User):
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

    from sqlalchemy import select as sa_select
    result = await db.execute(sa_select(Site))
    all_sites = list(result.scalars().all())
    active = [s for s in all_sites if s.status not in {SiteStatus.DESTROYED, SiteStatus.PENDING}]
    assert len(active) >= 1


async def test_destroyed_site_excluded_from_future(db: AsyncSession, admin_user: User):
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
    no_sleep = _daily_cost_for_site(_mock_site(sleep_mode=SleepMode.NONE))
    nightly = _daily_cost_for_site(_mock_site(sleep_mode=SleepMode.NIGHTLY))
    assert nightly < no_sleep * 0.6
