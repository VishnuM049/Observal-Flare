from __future__ import annotations

import re
import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.config import get_settings
from server.models.audit_log import AuditLog
from server.models.invite import Invite
from server.models.site import DeployType, Site, SiteStatus, SleepMode
from server.models.user import User, UserRole
from server.services.invite_service import InviteError, check_guest_site_limit

SLUG_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")

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
    settings = get_settings()

    existing = await db.execute(select(Site).where(Site.name == name, Site.status != SiteStatus.DESTROYED))
    if existing.scalar_one_or_none() is not None:
        raise SiteError(f"A site named '{name}' already exists")

    if user.role == UserRole.GUEST:
        await check_guest_site_limit(db, user)
        if user.invite_id:
            invite = await db.get(Invite, user.invite_id)
            if invite:
                if deploy_type.value not in invite.allowed_deploy_types:
                    raise InviteError(f"Deploy type '{deploy_type.value}' is not allowed for your invite")
                if instance_size not in invite.allowed_instance_sizes:
                    raise InviteError(f"Instance size '{instance_size}' is not allowed for your invite")
                if invite.env_overrides_locked and env_overrides:
                    raise InviteError("Environment overrides are locked for your invite")

    if auto_wipe_on_failure is None:
        auto_wipe_on_failure = deploy_type in (DeployType.PR, DeployType.BRANCH)

    if sleep_mode is None:
        if user.role == UserRole.GUEST:
            sleep_mode = SleepMode.NIGHTLY
        elif deploy_type in (DeployType.PR, DeployType.BRANCH):
            sleep_mode = SleepMode.IDLE
        else:
            sleep_mode = SleepMode.NONE

    ttl_days: int | None = None
    if deploy_type in (DeployType.PR, DeployType.BRANCH):
        ttl_days = 1
    if user.role == UserRole.GUEST and user.invite_id:
        invite = await db.get(Invite, user.invite_id)
        if invite and invite.forced_ttl_days is not None:
            ttl_days = invite.forced_ttl_days

    site = Site(
        name=name,
        domain=f"{name}.{settings.site_base_domain}",
        deploy_type=deploy_type,
        deploy_ref=deploy_ref,
        requestor_email=requestor_email,
        created_by=user.id,
        invite_id=user.invite_id if user.role == UserRole.GUEST else None,
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
    if user.role == UserRole.GUEST:
        stmt = stmt.where(Site.created_by == user.id)
    stmt = stmt.order_by(Site.created_at.desc())
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_site(db: AsyncSession, site_id: uuid.UUID, user: User) -> Site:
    site = await db.get(Site, site_id)
    if site is None:
        raise SiteError("Site not found")
    if user.role == UserRole.GUEST and site.created_by != user.id:
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
