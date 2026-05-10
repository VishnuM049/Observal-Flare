"""Tests for the provisioning pipeline: provision, destroy, redeploy."""
from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from server.mock import MockGitHubClient, MockSSM, MockTerraform
from server.models.site import DeployType, Site, SiteStatus, SleepMode
from server.models.user import User, UserRole
from server.provisioner import destroy_site, provision_site, redeploy_site
from server.ssm import CommandResult


class AlwaysFailSSM(MockSSM):
    """SSM mock that always fails."""

    async def run_command(self, instance_id: str, script: str, timeout_seconds: int = 600) -> CommandResult:
        return CommandResult(status="failed", output="deploy failed")


def _make_site(db: AsyncSession, user: User, **overrides) -> Site:
    defaults = dict(
        name=f"prov-{uuid.uuid4().hex[:6]}",
        domain="prov.observal.io",
        deploy_type=DeployType.BRANCH,
        deploy_ref="main",
        requestor_email="test@test.local",
        created_by=user.id,
        instance_size="t3.large",
        status=SiteStatus.PENDING,
        auto_wipe_on_failure=True,
    )
    defaults.update(overrides)
    site = Site(**defaults)
    db.add(site)
    return site


async def test_provision_pipeline(db: AsyncSession, admin_user: User):
    site = _make_site(db, admin_user)
    await db.commit()
    await db.refresh(site)

    result = await provision_site(
        db, site,
        infra=MockTerraform(),
        remote=MockSSM(),
        github=MockGitHubClient(),
    )

    assert result.status == SiteStatus.RUNNING
    assert result.resolved_sha is not None
    assert result.instance_id is not None
    assert result.ip_address is not None
    assert result.last_deployed_at is not None
    assert result.error_message is None


async def test_destroy_pipeline(db: AsyncSession, admin_user: User):
    site = _make_site(db, admin_user, status=SiteStatus.RUNNING, instance_id="i-mock-test", ip_address="127.0.0.1")
    await db.commit()
    await db.refresh(site)

    result = await destroy_site(
        db, site,
        infra=MockTerraform(),
        remote=MockSSM(),
    )

    assert result.status == SiteStatus.DESTROYED
    assert result.destroyed_at is not None
    assert result.instance_id is None
    assert result.ip_address is None


async def test_redeploy_preserves_data(db: AsyncSession, admin_user: User):
    site = _make_site(
        db, admin_user,
        status=SiteStatus.RUNNING,
        instance_id="i-mock-test",
        ip_address="127.0.0.1",
        auto_wipe_on_failure=True,
    )
    await db.commit()
    await db.refresh(site)

    result = await redeploy_site(
        db, site,
        remote=MockSSM(),
        github=MockGitHubClient(),
    )

    assert result.status == SiteStatus.RUNNING
    assert result.resolved_sha is not None
    assert result.last_deployed_at is not None
    assert result.error_message is None


async def test_redeploy_auto_wipe_on_failure(db: AsyncSession, admin_user: User):
    """When deploy succeeds but health check fails, and auto_wipe_on_failure=True,
    the provisioner should wipe volumes and retry. We mock _wait_for_healthy
    to fail first (triggering wipe) then succeed."""
    site = _make_site(
        db, admin_user,
        status=SiteStatus.RUNNING,
        instance_id="i-mock-test",
        ip_address="127.0.0.1",
        auto_wipe_on_failure=True,
    )
    await db.commit()
    await db.refresh(site)

    health_results = iter([False, True])

    with patch("server.provisioner._wait_for_healthy", new_callable=AsyncMock, side_effect=lambda *a, **kw: next(health_results)):
        result = await redeploy_site(
            db, site,
            remote=MockSSM(),
            github=MockGitHubClient(),
        )

    assert result.status == SiteStatus.RUNNING
    assert result.last_deployed_at is not None


async def test_redeploy_no_wipe_when_disabled(db: AsyncSession, admin_user: User):
    """When deploy succeeds but health check fails, and auto_wipe_on_failure=False,
    the provisioner should set status=failed without wiping."""
    site = _make_site(
        db, admin_user,
        status=SiteStatus.RUNNING,
        instance_id="i-mock-test",
        ip_address="127.0.0.1",
        auto_wipe_on_failure=False,
    )
    await db.commit()
    await db.refresh(site)

    with patch("server.provisioner._wait_for_healthy", new_callable=AsyncMock, return_value=False):
        with pytest.raises(RuntimeError, match="unhealthy after redeploy"):
            await redeploy_site(
                db, site,
                remote=MockSSM(),
                github=MockGitHubClient(),
            )

    await db.refresh(site)
    assert site.status == SiteStatus.FAILED
    assert site.error_message is not None
