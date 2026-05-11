"""ARQ task definitions for background provisioning jobs."""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from arq import cron, func
from sqlalchemy import select

from server.database import async_session
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
    if get_settings().is_local:
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
        transition_status(site, SiteStatus.STOPPING)
        await db.commit()

        await remote.run_command(site.instance_id, "cd /opt/observal && docker compose stop")

        site.status = SiteStatus.STOPPED
        await db.commit()
        logger.info("Site %s stopped", site.name)


async def task_start_site(ctx: dict, site_id: str) -> None:
    remote = _get_remote()
    async with async_session() as db:
        site = await db.get(Site, uuid.UUID(site_id))
        if site is None:
            return

        await remote.run_command(site.instance_id, "cd /opt/observal && docker compose start")
        site.status = SiteStatus.RUNNING
        await db.commit()
        logger.info("Site %s started", site.name)


async def cron_nightly_sleep(ctx: dict) -> None:
    """Stop containers on sleep_mode=nightly sites. Runs at 7 PM daily."""
    async with async_session() as db:
        result = await db.execute(
            select(Site).where(
                Site.status == SiteStatus.RUNNING,
                Site.sleep_mode == SleepMode.NIGHTLY,
            )
        )
        sites = list(result.scalars().all())
        if not sites:
            return

        remote = _get_remote()
        for site in sites:
            try:
                await remote.run_command(site.instance_id, "cd /opt/observal && docker compose stop")
                site.status = SiteStatus.SLEEPING
                await db.commit()
                logger.info("Nightly sleep: site %s now sleeping", site.name)
            except Exception:
                logger.exception("Nightly sleep failed for site %s", site.name)


async def cron_destroy_expired(ctx: dict) -> None:
    """Destroy sites past their scheduled_destroy_at. Runs hourly."""
    async with async_session() as db:
        now = datetime.now(timezone.utc)
        result = await db.execute(
            select(Site).where(
                Site.scheduled_destroy_at.isnot(None),
                Site.scheduled_destroy_at < now,
                Site.status.notin_([SiteStatus.DESTROYING, SiteStatus.DESTROYED]),
            )
        )
        sites = list(result.scalars().all())

        for site in sites:
            logger.info("Auto-teardown: destroying expired site %s", site.name)
            try:
                await destroy_site(db, site)
            except Exception:
                logger.exception("Auto-teardown failed for site %s", site.name)


async def cron_stale_reminders(ctx: dict) -> None:
    """Email requestors about sites past their TTL. Runs daily."""
    async with async_session() as db:
        now = datetime.now(timezone.utc)
        result = await db.execute(
            select(Site).where(
                Site.ttl_days.isnot(None),
                Site.status.in_([SiteStatus.RUNNING, SiteStatus.SLEEPING, SiteStatus.STOPPED]),
                Site.reminder_sent_at.is_(None),
            )
        )
        sites = list(result.scalars().all())

        for site in sites:
            age_days = (now - site.created_at.replace(tzinfo=timezone.utc)).days
            if age_days >= site.ttl_days:
                await send_site_notification(site, "stale")
                site.reminder_sent_at = now
                await db.commit()
                logger.info("Stale reminder sent for site %s (age=%dd, ttl=%dd)", site.name, age_days, site.ttl_days)


class WorkerSettings:
    functions = [
        func(task_provision_site, name="provision_site"),
        func(task_destroy_site, name="destroy_site"),
        func(task_redeploy_site, name="redeploy_site"),
        func(task_stop_site, name="stop_site"),
        func(task_start_site, name="start_site"),
    ]
    cron_jobs = [
        cron(cron_nightly_sleep, hour=19, minute=0),
        cron(cron_destroy_expired, minute=0),
        cron(cron_stale_reminders, hour=10, minute=0),
    ]
    job_timeout = 900
    max_jobs = 4

    redis_settings = get_redis_settings()
