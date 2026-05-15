from __future__ import annotations

import uuid
from datetime import datetime

from arq import ArqRedis
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, EmailStr, Field

from server.api.deps import DB, AdminUser, CurrentUser
from server.models.audit_log import AuditLog
from server.models.site import DeployType, Site, SiteStatus, SleepMode
from server.services.site_service import SiteError, audit_details, create_site, get_site, get_site_for_update, list_sites, validate_deploy_ref, validate_env_overrides

router = APIRouter(prefix="/api/sites", tags=["sites"])

_arq_pool: ArqRedis | None = None


def set_arq_pool(pool: ArqRedis) -> None:
    global _arq_pool
    _arq_pool = pool


def _get_pool() -> ArqRedis:
    if _arq_pool is None:
        raise HTTPException(status_code=503, detail="Worker pool not available")
    return _arq_pool


# --- Schemas ---


class SiteCreateRequest(BaseModel):
    name: str
    deploy_type: DeployType
    deploy_ref: str
    requestor_email: EmailStr
    instance_size: str = "t3.large"
    env_overrides: dict[str, str] | None = None
    auto_update: bool = False
    auto_wipe_on_failure: bool | None = None
    sleep_mode: SleepMode | None = None
    idle_timeout_minutes: int | None = Field(default=None, ge=15, le=480)
    sleep_at_hour: int | None = Field(default=None, ge=0, le=23)
    wake_at_hour: int | None = Field(default=None, ge=0, le=23)
    ttl_days: int | None = Field(default=None, ge=1, le=365)


class SiteUpdateRequest(BaseModel):
    deploy_ref: str | None = None
    env_overrides: dict[str, str] | None = None
    auto_update: bool | None = None
    auto_wipe_on_failure: bool | None = None
    sleep_mode: SleepMode | None = None
    idle_timeout_minutes: int | None = Field(default=None, ge=15, le=480)
    sleep_at_hour: int | None = Field(default=None, ge=0, le=23)
    wake_at_hour: int | None = Field(default=None, ge=0, le=23)
    ttl_days: int | None = Field(default=None, ge=0, le=365)
    requestor_email: EmailStr | None = None


class SiteResponse(BaseModel):
    id: uuid.UUID
    name: str
    domain: str
    status: SiteStatus
    requestor_email: str
    deploy_type: DeployType
    deploy_ref: str
    resolved_sha: str | None
    auto_update: bool
    auto_wipe_on_failure: bool
    sleep_mode: SleepMode
    idle_timeout_minutes: int
    sleep_at_hour: int
    wake_at_hour: int
    instance_size: str
    env_overrides: dict
    ip_address: str | None
    instance_id: str | None
    error_message: str | None
    ttl_days: int | None
    scheduled_destroy_at: datetime | None
    created_at: datetime
    updated_at: datetime
    last_activity_at: datetime | None
    last_deployed_at: datetime | None
    destroyed_at: datetime | None

    class Config:
        from_attributes = True


# --- Endpoints ---


@router.get("", response_model=list[SiteResponse])
async def list_all_sites(db: DB, user: CurrentUser):
    sites = await list_sites(db, user)
    return [SiteResponse.model_validate(s) for s in sites]


@router.post("", response_model=SiteResponse, status_code=201)
async def create_new_site(body: SiteCreateRequest, db: DB, user: CurrentUser):
    try:
        site = await create_site(
            db,
            user=user,
            name=body.name,
            deploy_type=body.deploy_type,
            deploy_ref=body.deploy_ref,
            requestor_email=body.requestor_email,
            instance_size=body.instance_size,
            env_overrides=body.env_overrides,
            auto_update=body.auto_update,
            auto_wipe_on_failure=body.auto_wipe_on_failure,
            sleep_mode=body.sleep_mode,
            idle_timeout_minutes=body.idle_timeout_minutes,
            sleep_at_hour=body.sleep_at_hour,
            wake_at_hour=body.wake_at_hour,
            ttl_days=body.ttl_days,
        )
    except SiteError as e:
        raise HTTPException(status_code=400, detail=str(e))

    pool = _get_pool()
    await pool.enqueue_job("provision_site", str(site.id))
    return SiteResponse.model_validate(site)


@router.get("/{site_id}", response_model=SiteResponse)
async def get_site_detail(site_id: uuid.UUID, db: DB, user: CurrentUser):
    try:
        site = await get_site(db, site_id, user)
    except SiteError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return SiteResponse.model_validate(site)


@router.patch("/{site_id}", response_model=SiteResponse)
async def update_site_config(site_id: uuid.UUID, body: SiteUpdateRequest, db: DB, user: CurrentUser):
    try:
        site = await get_site(db, site_id, user)
    except SiteError as e:
        raise HTTPException(status_code=404, detail=str(e))

    if body.deploy_ref is not None:
        try:
            validate_deploy_ref(body.deploy_ref)
        except SiteError as e:
            raise HTTPException(status_code=400, detail=str(e))
        site.deploy_ref = body.deploy_ref
    if body.env_overrides is not None:
        try:
            validate_env_overrides(body.env_overrides)
        except SiteError as e:
            raise HTTPException(status_code=400, detail=str(e))
        site.env_overrides = body.env_overrides
    if body.auto_update is not None:
        site.auto_update = body.auto_update
    if body.auto_wipe_on_failure is not None:
        site.auto_wipe_on_failure = body.auto_wipe_on_failure
    if body.sleep_mode is not None:
        site.sleep_mode = body.sleep_mode
    if body.idle_timeout_minutes is not None:
        site.idle_timeout_minutes = body.idle_timeout_minutes
    if body.sleep_at_hour is not None:
        site.sleep_at_hour = body.sleep_at_hour
    if body.wake_at_hour is not None:
        site.wake_at_hour = body.wake_at_hour
    if body.requestor_email is not None:
        site.requestor_email = body.requestor_email
    if body.ttl_days is not None:
        if body.ttl_days == 0:
            site.ttl_days = None
            site.reminder_sent_at = None
            site.scheduled_destroy_at = None
        else:
            site.ttl_days = body.ttl_days
            site.reminder_sent_at = None
            site.scheduled_destroy_at = None

    await db.commit()
    await db.refresh(site)
    return SiteResponse.model_validate(site)


@router.post("/{site_id}/redeploy", response_model=SiteResponse)
async def redeploy(site_id: uuid.UUID, db: DB, user: CurrentUser):
    try:
        site = await get_site_for_update(db, site_id, user)
    except SiteError as e:
        raise HTTPException(status_code=404, detail=str(e))
    if site.status not in (SiteStatus.RUNNING, SiteStatus.SLEEPING, SiteStatus.FAILED):
        raise HTTPException(status_code=400, detail=f"Cannot redeploy from status {site.status.value}")

    db.add(AuditLog(user_id=user.id, site_id=site.id, action="site.redeploy_requested", details=audit_details(site, from_status=site.status.value)))
    pool = _get_pool()
    await pool.enqueue_job("redeploy_site", str(site.id))
    await db.commit()
    return SiteResponse.model_validate(site)


@router.post("/{site_id}/stop", response_model=SiteResponse)
async def stop_site(site_id: uuid.UUID, db: DB, user: CurrentUser):
    try:
        site = await get_site_for_update(db, site_id, user)
    except SiteError as e:
        raise HTTPException(status_code=404, detail=str(e))
    if site.status != SiteStatus.RUNNING:
        raise HTTPException(status_code=400, detail="Site is not running")

    db.add(AuditLog(user_id=user.id, site_id=site.id, action="site.stop_requested", details=audit_details(site)))
    pool = _get_pool()
    await pool.enqueue_job("stop_site", str(site.id))
    await db.commit()
    return SiteResponse.model_validate(site)


@router.post("/{site_id}/start", response_model=SiteResponse)
async def start_site(site_id: uuid.UUID, db: DB, user: CurrentUser):
    try:
        site = await get_site_for_update(db, site_id, user)
    except SiteError as e:
        raise HTTPException(status_code=404, detail=str(e))
    if site.status not in (SiteStatus.STOPPED, SiteStatus.SLEEPING):
        raise HTTPException(status_code=400, detail="Site is not stopped or sleeping")

    db.add(AuditLog(user_id=user.id, site_id=site.id, action="site.start_requested", details=audit_details(site)))
    pool = _get_pool()
    await pool.enqueue_job("start_site", str(site.id))
    await db.commit()
    return SiteResponse.model_validate(site)


@router.post("/{site_id}/destroy", response_model=SiteResponse)
async def destroy(site_id: uuid.UUID, db: DB, user: CurrentUser):
    try:
        site = await get_site_for_update(db, site_id, user)
    except SiteError as e:
        raise HTTPException(status_code=404, detail=str(e))
    if site.status in (SiteStatus.DESTROYING, SiteStatus.DESTROYED):
        raise HTTPException(status_code=400, detail="Site is already being destroyed or destroyed")

    db.add(AuditLog(user_id=user.id, site_id=site.id, action="site.destroy_requested", details=audit_details(site, from_status=site.status.value)))
    pool = _get_pool()
    await pool.enqueue_job("destroy_site", str(site.id))
    await db.commit()
    return SiteResponse.model_validate(site)


@router.post("/{site_id}/idle", status_code=204)
async def report_idle(site_id: uuid.UUID, db: DB, authorization: str | None = Header(None)):
    """Called by the instance itself when it detects no traffic for 2 hours."""
    site = await db.get(Site, site_id)
    if site is None:
        raise HTTPException(status_code=404)
    if not site.idle_token or authorization != f"Bearer {site.idle_token}":
        raise HTTPException(status_code=401, detail="Invalid idle token")
    if site.status != SiteStatus.RUNNING or site.sleep_mode != SleepMode.IDLE:
        return
    pool = _get_pool()
    await pool.enqueue_job("sleep_site", str(site.id))


@router.post("/{site_id}/heartbeat", status_code=204)
async def report_heartbeat(site_id: uuid.UUID, body: dict, db: DB, authorization: str | None = Header(None)):
    """Called by the instance every 15 min with last request timestamp."""
    from datetime import datetime, timezone
    site = await db.get(Site, site_id)
    if site is None:
        raise HTTPException(status_code=404)
    if not site.idle_token or authorization != f"Bearer {site.idle_token}":
        raise HTTPException(status_code=401, detail="Invalid token")
    ts = body.get("last_request_ts")
    if ts:
        site.last_activity_at = datetime.fromtimestamp(ts, tz=timezone.utc)
    else:
        site.last_activity_at = datetime.now(timezone.utc)
    await db.commit()


@router.post("/{site_id}/unlock", status_code=204)
async def force_unlock(site_id: uuid.UUID, body: dict, db: DB, admin: AdminUser):
    try:
        site = await get_site(db, site_id, admin)
    except SiteError as e:
        raise HTTPException(status_code=404, detail=str(e))

    lock_id = body.get("lock_id")
    if not lock_id:
        raise HTTPException(status_code=400, detail="lock_id required")

    from server.config import get_settings
    from server.mock import MockTerraform
    from server.terraform import RealTerraform

    tf = MockTerraform() if get_settings().use_mock_terraform else RealTerraform()
    await tf.force_unlock(site.name, lock_id)


