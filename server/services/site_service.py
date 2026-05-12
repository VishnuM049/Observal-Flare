from __future__ import annotations

import re
import secrets
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.config import get_settings
from server.models.audit_log import AuditLog
from server.models.site import DeployType, Site, SiteStatus, SleepMode
from server.models.user import User

SLUG_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
DEPLOY_REF_RE = re.compile(r"^[a-zA-Z0-9._/\-]+$")
ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

VALID_INSTANCE_SIZES = frozenset({"t3.medium", "t3.large", "t3.xlarge"})

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


def validate_deploy_ref(deploy_ref: str) -> None:
    if not deploy_ref or len(deploy_ref) > 255:
        raise SiteError("Deploy ref must be 1-255 characters")
    if not DEPLOY_REF_RE.match(deploy_ref):
        raise SiteError("Deploy ref contains invalid characters — only alphanumeric, dots, slashes, hyphens, underscores allowed")


def validate_instance_size(instance_size: str) -> None:
    if instance_size not in VALID_INSTANCE_SIZES:
        raise SiteError(f"Invalid instance size: '{instance_size}' — allowed: {', '.join(sorted(VALID_INSTANCE_SIZES))}")


def audit_details(site: "Site", **extra: str | int | None) -> dict:
    """Build a standard details dict for audit log entries."""
    d: dict = {
        "name": site.name,
        "deploy_type": site.deploy_type.value,
        "deploy_ref": site.deploy_ref,
        "instance_size": site.instance_size,
        "sleep_mode": site.sleep_mode.value,
    }
    d.update({k: v for k, v in extra.items() if v is not None})
    return d


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
    idle_timeout_minutes: int | None = None,
    sleep_at_hour: int | None = None,
    wake_at_hour: int | None = None,
    ttl_days: int | None = None,
) -> Site:
    validate_site_name(name)
    validate_deploy_ref(deploy_ref)
    validate_instance_size(instance_size)
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

    if ttl_days is None and deploy_type in (DeployType.PR, DeployType.BRANCH):
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
        idle_timeout_minutes=idle_timeout_minutes or 120,
        sleep_at_hour=sleep_at_hour if sleep_at_hour is not None else 19,
        wake_at_hour=wake_at_hour if wake_at_hour is not None else 7,
        ttl_days=ttl_days,
        terraform_state_key=f"sites/{name}/terraform.tfstate",
        idle_token=secrets.token_urlsafe(32),
    )
    db.add(site)
    await db.flush()

    audit = AuditLog(user_id=user.id, site_id=site.id, action="site.created", details=audit_details(site, lifetime=f"{ttl_days}d" if ttl_days else "unlimited"))
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
    # Access is team-wide by design — user param reserved for future scoping.
    site = await db.get(Site, site_id)
    if site is None:
        raise SiteError("Site not found")
    return site


async def get_site_for_update(db: AsyncSession, site_id: uuid.UUID, user: User) -> Site:
    """Get a site with a row-level lock to prevent concurrent operations.
    Access is team-wide by design — user param reserved for future scoping."""
    result = await db.execute(
        select(Site).where(Site.id == site_id).with_for_update()
    )
    site = result.scalar_one_or_none()
    if site is None:
        raise SiteError("Site not found")
    return site


def transition_status(site: Site, new_status: SiteStatus) -> None:
    allowed = VALID_STATUS_TRANSITIONS.get(site.status, set())
    if new_status not in allowed:
        raise SiteError(f"Cannot transition from {site.status.value} to {new_status.value}")
    site.status = new_status
    site.updated_at = datetime.now(timezone.utc)


async def update_site_status(db: AsyncSession, site: Site, new_status: SiteStatus, **kwargs: str | None) -> Site:
    transition_status(site, new_status)
    for key, value in kwargs.items():
        if hasattr(site, key):
            setattr(site, key, value)
    await db.commit()
    await db.refresh(site)
    return site
