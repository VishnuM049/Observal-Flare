"""ARQ task definitions for background provisioning jobs."""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

from arq import cron, func
from sqlalchemy import select

from server.database import async_session
from server.events import publish_site_event
from server.models.audit_log import AuditLog
from server.models.site import Site, SiteStatus, SleepMode
from server.notifications.email import send_site_notification
from server.provisioner import destroy_site, provision_site, redeploy_site
from server.services.site_service import transition_status
from server.ssm import SSMRunner
from server.mock import MockSSM
from server.config import get_settings
from server.ssm import RealSSM
from server.worker.settings import get_redis_settings

logger = logging.getLogger(__name__)


def _get_remote() -> SSMRunner:
    if get_settings().use_mock_ssm:
        return MockSSM()
    return RealSSM()


async def task_provision_site(ctx: dict, site_id: str) -> None:
    async with async_session() as db:
        site = await db.get(Site, uuid.UUID(site_id))
        if site is None:
            logger.error("Site %s not found for provisioning", site_id)
            return
        await provision_site(db, site)


async def task_destroy_site(ctx: dict, site_id: str) -> None:
    async with async_session() as db:
        site = await db.get(Site, uuid.UUID(site_id))
        if site is None:
            logger.error("Site %s not found for destroy", site_id)
            return
        await destroy_site(db, site)


async def task_redeploy_site(ctx: dict, site_id: str) -> None:
    async with async_session() as db:
        site = await db.get(Site, uuid.UUID(site_id))
        if site is None:
            logger.error("Site %s not found for redeploy", site_id)
            return
        await redeploy_site(db, site)


async def task_stop_site(ctx: dict, site_id: str) -> None:
    remote = _get_remote()
    async with async_session() as db:
        site = await db.get(Site, uuid.UUID(site_id))
        if site is None:
            return
        try:
            transition_status(site, SiteStatus.STOPPING)
            await db.commit()
            await publish_site_event(site_id, "status_change", status="stopping", message="Stopping site...")

            await remote.run_command(site.instance_id, "cd /opt/observal && docker compose stop")

            site.status = SiteStatus.STOPPED
            db.add(AuditLog(user_id=site.created_by, site_id=site.id, action="site.stopped", details={"name": site.name, "instance_size": site.instance_size}))
            await db.commit()
            await publish_site_event(site_id, "status_change", status="stopped", message="Site stopped")
            logger.info("Site %s stopped", site.name)
        except Exception as e:
            logger.exception("Stop failed for site %s", site_id)
            site.status = SiteStatus.FAILED
            site.error_message = str(e)[:2000]
            await db.commit()
            await publish_site_event(site_id, "error", status="failed", message=str(e)[:200])


async def task_start_site(ctx: dict, site_id: str) -> None:
    remote = _get_remote()
    async with async_session() as db:
        site = await db.get(Site, uuid.UUID(site_id))
        if site is None:
            return
        try:
            await remote.run_command(site.instance_id, "cd /opt/observal && docker compose start")
            site.status = SiteStatus.RUNNING
            db.add(AuditLog(user_id=site.created_by, site_id=site.id, action="site.started", details={"name": site.name, "instance_size": site.instance_size}))
            await db.commit()
            await publish_site_event(site_id, "status_change", status="running", message="Site started")
            logger.info("Site %s started", site.name)
        except Exception as e:
            logger.exception("Start failed for site %s", site_id)
            site.status = SiteStatus.FAILED
            site.error_message = str(e)[:2000]
            await db.commit()
            await publish_site_event(site_id, "error", status="failed", message=str(e)[:200])


async def task_sleep_site(ctx: dict, site_id: str) -> None:
    """Stop containers and mark site as sleeping (used by idle detection)."""
    remote = _get_remote()
    async with async_session() as db:
        site = await db.get(Site, uuid.UUID(site_id))
        if site is None:
            return
        if site.status != SiteStatus.RUNNING:
            return
        try:
            transition_status(site, SiteStatus.SLEEPING)
            await db.commit()
            await publish_site_event(site_id, "status_change", status="sleeping", message="Site going to sleep")

            await remote.run_command(site.instance_id, "cd /opt/observal && docker compose stop")
            await db.commit()
            logger.info("Idle sleep: site %s now sleeping", site.name)
        except Exception as e:
            logger.exception("Sleep failed for site %s", site_id)
            site.status = SiteStatus.FAILED
            site.error_message = str(e)[:2000]
            await db.commit()
            await publish_site_event(site_id, "error", status="failed", message=str(e)[:200])


async def cron_nightly_sleep(ctx: dict) -> None:
    """Stop containers on sleep_mode=nightly sites. Runs at 7 PM daily."""
    async with async_session() as db:
        result = await db.execute(
            select(Site.id).where(
                Site.status == SiteStatus.RUNNING,
                Site.sleep_mode == SleepMode.NIGHTLY,
            )
        )
        site_ids = list(result.scalars().all())

    remote = _get_remote()
    for site_id in site_ids:
        try:
            async with async_session() as db:
                site = await db.get(Site, site_id)
                if site is None or site.status != SiteStatus.RUNNING:
                    continue
                await remote.run_command(site.instance_id, "cd /opt/observal && docker compose stop")
                site.status = SiteStatus.SLEEPING
                await db.commit()
                logger.info("Nightly sleep: site %s now sleeping", site.name)
        except Exception:
            logger.exception("Nightly sleep failed for site %s", site_id)


async def cron_destroy_expired(ctx: dict) -> None:
    """Destroy sites past their scheduled_destroy_at. Runs hourly."""
    async with async_session() as db:
        now = datetime.now(timezone.utc)
        result = await db.execute(
            select(Site.id).where(
                Site.scheduled_destroy_at.isnot(None),
                Site.scheduled_destroy_at < now,
                Site.status.notin_([SiteStatus.DESTROYING, SiteStatus.DESTROYED]),
            )
        )
        site_ids = list(result.scalars().all())

    for site_id in site_ids:
        try:
            async with async_session() as db:
                site = await db.get(Site, site_id)
                if site is None:
                    continue
                logger.info("Auto-teardown: destroying expired site %s", site.name)
                await destroy_site(db, site)
        except Exception:
            logger.exception("Auto-teardown failed for site %s", site_id)


async def cron_stale_reminders(ctx: dict) -> None:
    """Warn about expired TTL and schedule destruction 12h later. Runs daily."""
    now = datetime.now(timezone.utc)
    async with async_session() as db:
        result = await db.execute(
            select(Site.id, Site.ttl_days, Site.created_at, Site.name).where(
                Site.ttl_days.isnot(None),
                Site.status.in_([SiteStatus.RUNNING, SiteStatus.SLEEPING, SiteStatus.STOPPED]),
                Site.reminder_sent_at.is_(None),
                Site.scheduled_destroy_at.is_(None),
            )
        )
        candidates = list(result.all())

    for row in candidates:
        created = row.created_at if row.created_at.tzinfo else row.created_at.replace(tzinfo=timezone.utc)
        age_days = (now - created).days
        if age_days < row.ttl_days:
            continue
        try:
            async with async_session() as db:
                site = await db.get(Site, row.id)
                if site is None:
                    continue
                site.reminder_sent_at = now
                site.scheduled_destroy_at = now + timedelta(hours=12)
                await db.commit()
                await send_site_notification(site, "ttl_expiring")
                logger.info(
                    "TTL expiry: site %s scheduled for destruction in 12h (age=%dd, ttl=%dd)",
                    site.name, age_days, row.ttl_days,
                )
        except Exception:
            logger.exception("Stale reminder failed for site %s", row.name)


class WorkerSettings:
    functions = [
        func(task_provision_site, name="provision_site"),
        func(task_destroy_site, name="destroy_site"),
        func(task_redeploy_site, name="redeploy_site"),
        func(task_stop_site, name="stop_site"),
        func(task_start_site, name="start_site"),
        func(task_sleep_site, name="sleep_site"),
    ]
    cron_jobs = [
        cron(cron_nightly_sleep, hour=19, minute=0),
        cron(cron_destroy_expired, minute=0),
        cron(cron_stale_reminders, hour=10, minute=0),
    ]
    job_timeout = 900
    max_jobs = 4

    redis_settings = get_redis_settings()
