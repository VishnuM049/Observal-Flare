from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import select

from server.api.deps import DB, AdminUser
from server.models.audit_log import AuditLog
from server.models.site import Site, SiteStatus, SleepMode

router = APIRouter(prefix="/api/costs", tags=["costs"])

# AWS pricing — update periodically from https://aws.amazon.com/ec2/pricing/
EC2_MONTHLY: dict[str, float] = {
    "t3.medium": 28.80,
    "t3.large": 57.60,
    "t3.xlarge": 180.00,
    "t3.2xlarge": 288.00,
}
EBS_MONTHLY = 4.0
DATA_TRANSFER_MONTHLY = 2.0
EIP_STOPPED_MONTHLY = 3.6

# GCP pricing — update periodically from https://cloud.google.com/compute/pricing
GCE_MONTHLY: dict[str, float] = {
    "e2-medium": 24.27,
    "e2-standard-2": 48.54,
    "e2-standard-4": 97.09,
    "e2-standard-8": 194.18,
}
GCE_DISK_MONTHLY = 3.40
GCE_DATA_TRANSFER_MONTHLY = 1.50
GCE_STATIC_IP_STOPPED_MONTHLY = 2.88

RUNNING_ACTIONS = {"site.started", "site.created", "site.redeployed"}
STOPPED_ACTIONS = {"site.stopped", "site.sleeping", "site.destroyed"}

BILLABLE_STATUSES = {
    SiteStatus.RUNNING,
    SiteStatus.DEPLOYING,
    SiteStatus.PROVISIONING,
    SiteStatus.STOPPING,
    SiteStatus.SLEEPING,
    SiteStatus.STOPPED,
    SiteStatus.FAILED,
}


def _hourly_rate(instance_size: str, cloud_provider: str = "aws") -> float:
    if cloud_provider == "gcp":
        monthly = GCE_MONTHLY.get(instance_size, GCE_MONTHLY["e2-standard-2"])
    else:
        monthly = EC2_MONTHLY.get(instance_size, EC2_MONTHLY["t3.large"])
    return monthly / 30 / 24


def _fixed_daily_cost(cloud_provider: str = "aws") -> float:
    if cloud_provider == "gcp":
        return (GCE_DISK_MONTHLY + GCE_DATA_TRANSFER_MONTHLY) / 30
    return (EBS_MONTHLY + DATA_TRANSFER_MONTHLY) / 30


def _eip_stopped_daily(cloud_provider: str = "aws") -> float:
    if cloud_provider == "gcp":
        return GCE_STATIC_IP_STOPPED_MONTHLY / 30
    return EIP_STOPPED_MONTHLY / 30


def _running_fraction(site: Site) -> float:
    if site.sleep_mode == SleepMode.NIGHTLY:
        wake = site.wake_at_hour
        sleep = site.sleep_at_hour
        if sleep > wake:
            hours = sleep - wake
        elif sleep < wake:
            hours = 24 - wake + sleep
        else:
            hours = 24
        return hours / 24
    elif site.sleep_mode == SleepMode.IDLE:
        return 0.42
    return 1.0


def _daily_cost_for_site(site: Site) -> float:
    provider = getattr(site, "cloud_provider", "aws")
    fraction = _running_fraction(site)
    running_cost = _hourly_rate(site.instance_size, provider) * 24 * fraction
    eip_cost = _eip_stopped_daily(provider) * (1 - fraction) if fraction < 1 else 0
    return running_cost + _fixed_daily_cost(provider) + eip_cost


def _projected_end_date(site: Site) -> datetime | None:
    if site.scheduled_destroy_at is not None:
        dt = site.scheduled_destroy_at
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
    if site.ttl_days is not None and site.reminder_sent_at is None:
        created = site.created_at
        created = created.replace(tzinfo=timezone.utc) if created.tzinfo is None else created
        return created + timedelta(days=site.ttl_days, hours=12)
    return None


def _ensure_utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


async def _compute_historical_cost(
    db, site: Site, day_start: datetime, day_end: datetime, audit_logs: list[AuditLog]
) -> float:
    """Compute cost for a site on a given day using audit log transitions."""
    created = _ensure_utc(site.created_at)
    if created >= day_end:
        return 0.0
    if site.destroyed_at is not None:
        destroyed = _ensure_utc(site.destroyed_at)
        if destroyed <= day_start:
            return 0.0

    site_logs = [
        l for l in audit_logs
        if l.site_id == site.id and day_start <= _ensure_utc(l.created_at) < day_end
    ]
    site_logs.sort(key=lambda l: l.created_at)

    if not site_logs:
        return _daily_cost_for_site(site)

    running_seconds = 0.0
    is_running = True
    segment_start = max(created, day_start)

    for log in site_logs:
        log_time = _ensure_utc(log.created_at)
        if log.action in STOPPED_ACTIONS and is_running:
            running_seconds += (log_time - segment_start).total_seconds()
            is_running = False
            segment_start = log_time
        elif log.action in RUNNING_ACTIONS and not is_running:
            is_running = True
            segment_start = log_time

    if is_running:
        end = min(day_end, _ensure_utc(site.destroyed_at) if site.destroyed_at else day_end)
        running_seconds += (end - segment_start).total_seconds()

    total_seconds = (min(day_end, _ensure_utc(site.destroyed_at) if site.destroyed_at else day_end) - max(created, day_start)).total_seconds()
    if total_seconds <= 0:
        return 0.0

    running_fraction = running_seconds / total_seconds
    alive_days = total_seconds / 86400

    provider = getattr(site, "cloud_provider", "aws")
    running_cost = _hourly_rate(site.instance_size, provider) * (running_seconds / 3600)
    eip_stopped_cost = _eip_stopped_daily(provider) * (1 - running_fraction) * alive_days
    fixed_cost = _fixed_daily_cost(provider) * alive_days

    return running_cost + eip_stopped_cost + fixed_cost


class DayCost(BaseModel):
    date: str
    cost: float
    site_count: int


class CostSummary(BaseModel):
    history: list[DayCost]
    projection: list[DayCost]
    today_daily: float
    today_site_count: int


@router.get("", response_model=CostSummary)
async def get_cost_summary(
    db: DB,
    admin: AdminUser,
    history_days: int = Query(30, ge=1, le=90),
    projection_days: int = Query(14, ge=1, le=30),
):
    result = await db.execute(select(Site))
    all_sites = list(result.scalars().all())

    today = date.today()
    history_start = today - timedelta(days=history_days - 1)
    history_start_dt = datetime(history_start.year, history_start.month, history_start.day, tzinfo=timezone.utc)

    log_result = await db.execute(
        select(AuditLog)
        .where(AuditLog.created_at >= history_start_dt)
        .where(AuditLog.site_id.isnot(None))
        .where(AuditLog.action.in_(list(RUNNING_ACTIONS | STOPPED_ACTIONS)))
    )
    all_logs = list(log_result.scalars().all())

    history: list[DayCost] = []
    for i in range(history_days):
        day = history_start + timedelta(days=i)
        day_start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
        day_end = day_start + timedelta(days=1)

        total = 0.0
        count = 0
        for site in all_sites:
            cost = await _compute_historical_cost(db, site, day_start, day_end, all_logs)
            if cost > 0:
                total += cost
                count += 1

        history.append(DayCost(date=day.isoformat(), cost=round(total, 2), site_count=count))

    active_sites = [s for s in all_sites if s.status in BILLABLE_STATUSES]

    idle_fractions: dict[uuid.UUID, float] = {}
    lookback_start = datetime(today.year, today.month, today.day, tzinfo=timezone.utc) - timedelta(days=7)
    lookback_end = datetime(today.year, today.month, today.day, tzinfo=timezone.utc)
    for site in active_sites:
        if site.sleep_mode != SleepMode.IDLE:
            continue
        cost = await _compute_historical_cost(db, site, lookback_start, lookback_end, all_logs)
        provider = getattr(site, "cloud_provider", "aws")
        full_cost = _hourly_rate(site.instance_size, provider) * 7 * 24 + _fixed_daily_cost(provider) * 7
        if full_cost > 0:
            idle_fractions[site.id] = min(cost / full_cost, 1.0)

    def _projected_daily_cost(site: Site) -> float:
        provider = getattr(site, "cloud_provider", "aws")
        if site.sleep_mode == SleepMode.IDLE and site.id in idle_fractions:
            fraction = idle_fractions[site.id]
            running_cost = _hourly_rate(site.instance_size, provider) * 24 * fraction
            eip_cost = _eip_stopped_daily(provider) * (1 - fraction) if fraction < 1 else 0
            return running_cost + _fixed_daily_cost(provider) + eip_cost
        return _daily_cost_for_site(site)

    today_daily = sum(_projected_daily_cost(s) for s in active_sites)

    projection: list[DayCost] = []
    for i in range(1, projection_days + 1):
        day = today + timedelta(days=i)
        day_start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
        day_cost = 0.0
        day_count = 0
        for site in active_sites:
            end = _projected_end_date(site)
            if end is not None and end <= day_start:
                continue
            day_cost += _projected_daily_cost(site)
            day_count += 1
        projection.append(DayCost(
            date=day.isoformat(),
            cost=round(day_cost, 2),
            site_count=day_count,
        ))

    return CostSummary(
        history=history,
        projection=projection,
        today_daily=round(today_daily, 2),
        today_site_count=len(active_sites),
    )
