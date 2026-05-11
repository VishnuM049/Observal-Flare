from __future__ import annotations

import re
import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.config import get_settings
from server.models.audit_log import AuditLog
from server.models.site import DeployType, Site, SiteStatus, SleepMode
from server.models.user import User

SLUG_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

RESERVED_ENV_KEYS = frozenset({
    "DATABASE_URL",
    "SECRET_KEY",
    "CLICKHOUSE_URL",
    "REDIS_URL",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "CLICKHOUSE_USER",
    "CLICKHOUSE_PASSWORD",
})

VALID_STATUS_TRANSITIONS: dict[SiteStatus, set[SiteStatus]] = {
    SiteStatus.PENDING: {SiteStatus.PROVISIONING, SiteStatus.FAILED},
    SiteStatus.PROVISIONING: {SiteStatus.DEPLOYING, SiteStatus.FAILED},
    SiteStatus.DEPLOYING: {SiteStatus.RUNNING, SiteStatus.FAILED},
    SiteStatus.RUNNING: {SiteStatus.STOPPING, SiteStatus.SLEEPING, SiteStatus.DEPLOYING, SiteStatus.DESTROYING, SiteStatus.FAILED},
    SiteStatus.STOPPING: {SiteStatus.STOPPED, SiteStatus.FAILED},
    SiteStatus.STOPPED: {SiteStatus.RUNNING, SiteStatus.DESTROYING},
    SiteStatus.SLEEPING: {SiteStatus.RUNNING, SiteStatus.DEPLOYING, SiteStatus.DESTROYING},
    SiteStatus.DESTROYING: {SiteStatus.DESTROYED, SiteStatus.FAILED},
    SiteStatus.DESTROYED: set(),
    SiteStatus.FAILED: {SiteStatus.PROVISIONING, SiteStatus.DEPLOYING, SiteStatus.DESTROYING},
}


class SiteError(Exception):
    pass


def validate_site_name(name: str) -> None:
    if not SLUG_RE.match(name):
        raise SiteError("Site name must be lowercase alphanumeric with hyphens, 1-63 chars")


def validate_env_overrides(overrides: dict[str, str]) -> None:
    for key, value in overrides.items():
        if not ENV_KEY_RE.match(key):
            raise SiteError(f"Invalid env var key: '{key}' — must be alphanumeric/underscores, starting with a letter or underscore")
        if key in RESERVED_ENV_KEYS:
            raise SiteError(f"Cannot override reserved key: '{key}' — managed by Flare")
        if "\n" in value or "\r" in value:
            raise SiteError(f"Env var '{key}' contains newlines — not allowed")


async def create_site(
    db: AsyncSession,
    *,
    user: User,
    name: str,
    deploy_type: DeployType,
    deploy_ref: str,
    requestor_email: str,
    instance_size: str = "t3.large",
    env_overrides: dict | None = None,
    auto_update: bool = False,
    auto_wipe_on_failure: bool | None = None,
    sleep_mode: SleepMode | None = None,
) -> Site:
    validate_site_name(name)
    if env_overrides:
        validate_env_overrides(env_overrides)
    settings = get_settings()

    existing = await db.execute(select(Site).where(Site.name == name, Site.status != SiteStatus.DESTROYED))
    if existing.scalar_one_or_none() is not None:
        raise SiteError(f"A site named '{name}' already exists")

    if auto_wipe_on_failure is None:
        auto_wipe_on_failure = deploy_type in (DeployType.PR, DeployType.BRANCH)

    if sleep_mode is None:
        if deploy_type in (DeployType.PR, DeployType.BRANCH):
            sleep_mode = SleepMode.IDLE
        else:
            sleep_mode = SleepMode.NONE

    ttl_days: int | None = None
    if deploy_type in (DeployType.PR, DeployType.BRANCH):
        ttl_days = 1

    site = Site(
        name=name,
        domain=f"{name}.{settings.site_base_domain}",
        deploy_type=deploy_type,
        deploy_ref=deploy_ref,
        requestor_email=requestor_email,
        created_by=user.id,
        instance_size=instance_size,
        env_overrides=env_overrides or {},
        auto_update=auto_update,
        auto_wipe_on_failure=auto_wipe_on_failure,
        sleep_mode=sleep_mode,
        ttl_days=ttl_days,
        terraform_state_key=f"sites/{name}/terraform.tfstate",
    )
    db.add(site)

    audit = AuditLog(user_id=user.id, site_id=site.id, action="site.created", details={"name": name, "deploy_type": deploy_type.value, "deploy_ref": deploy_ref})
    db.add(audit)

    await db.commit()
    await db.refresh(site)
    return site


async def list_sites(db: AsyncSession, user: User) -> list[Site]:
    stmt = select(Site).where(Site.status != SiteStatus.DESTROYED)
    stmt = stmt.order_by(Site.created_at.desc())
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_site(db: AsyncSession, site_id: uuid.UUID, user: User) -> Site:
    site = await db.get(Site, site_id)
    if site is None:
        raise SiteError("Site not found")
    return site


def transition_status(site: Site, new_status: SiteStatus) -> None:
    allowed = VALID_STATUS_TRANSITIONS.get(site.status, set())
    if new_status not in allowed:
        raise SiteError(f"Cannot transition from {site.status.value} to {new_status.value}")
    site.status = new_status
    site.updated_at = datetime.utcnow()


async def update_site_status(db: AsyncSession, site: Site, new_status: SiteStatus, **kwargs: str | None) -> Site:
    transition_status(site, new_status)
    for key, value in kwargs.items():
        if hasattr(site, key):
            setattr(site, key, value)
    await db.commit()
    await db.refresh(site)
    return site
