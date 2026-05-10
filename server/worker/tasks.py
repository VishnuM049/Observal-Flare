"""ARQ task definitions for background provisioning jobs."""
from __future__ import annotations

import logging
import uuid

from arq import func

from server.database import async_session
from server.models.site import Site, SiteStatus
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


class WorkerSettings:
    functions = [
        func(task_provision_site, name="provision_site"),
        func(task_destroy_site, name="destroy_site"),
        func(task_redeploy_site, name="redeploy_site"),
        func(task_stop_site, name="stop_site"),
        func(task_start_site, name="start_site"),
    ]
    job_timeout = 900
    max_jobs = 4

    redis_settings = get_redis_settings()
