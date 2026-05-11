from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import select

from server.api.deps import DB, AdminUser
from server.models.site import Site, SiteStatus, SleepMode

router = APIRouter(prefix="/api/costs", tags=["costs"])

EC2_MONTHLY: dict[str, float] = {
    "t3.medium": 30,
    "t3.large": 54,
    "t3.xlarge": 108,
}

EBS_MONTHLY = 4.0
DATA_TRANSFER_MONTHLY = 2.0
EIP_STOPPED_MONTHLY = 3.6

SLEEP_RUNNING_FRACTION: dict[str, float] = {
    "none": 1.0,
    "nightly": 0.42,
    "idle": 0.42,
}

BILLABLE_STATUSES = {
    SiteStatus.RUNNING,
    SiteStatus.DEPLOYING,
    SiteStatus.PROVISIONING,
    SiteStatus.STOPPING,
    SiteStatus.SLEEPING,
    SiteStatus.STOPPED,
    SiteStatus.FAILED,
}


def _daily_cost(instance_size: str, sleep_mode: str) -> float:
    ec2 = EC2_MONTHLY.get(instance_size, EC2_MONTHLY["t3.large"])
    fraction = SLEEP_RUNNING_FRACTION.get(sleep_mode, 1.0)
    ec2_cost = ec2 * fraction
    eip_cost = EIP_STOPPED_MONTHLY * (1 - fraction) if fraction < 1 else 0
    return (ec2_cost + EBS_MONTHLY + DATA_TRANSFER_MONTHLY + eip_cost) / 30


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

    history: list[DayCost] = []
    for i in range(history_days):
        day = history_start + timedelta(days=i)
        day_start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
        day_end = day_start + timedelta(days=1)

        total = 0.0
        count = 0
        for site in all_sites:
            created = site.created_at.replace(tzinfo=timezone.utc) if site.created_at.tzinfo is None else site.created_at
            if created >= day_end:
                continue
            if site.destroyed_at is not None:
                destroyed = site.destroyed_at.replace(tzinfo=timezone.utc) if site.destroyed_at.tzinfo is None else site.destroyed_at
                if destroyed <= day_start:
                    continue
            total += _daily_cost(site.instance_size, site.sleep_mode.value)
            count += 1

        history.append(DayCost(date=day.isoformat(), cost=round(total, 2), site_count=count))

    active_sites = [
        s for s in all_sites
        if s.status in BILLABLE_STATUSES
    ]
    today_daily = sum(_daily_cost(s.instance_size, s.sleep_mode.value) for s in active_sites)

    projection: list[DayCost] = []
    for i in range(1, projection_days + 1):
        day = today + timedelta(days=i)
        projection.append(DayCost(
            date=day.isoformat(),
            cost=round(today_daily, 2),
            site_count=len(active_sites),
        ))

    return CostSummary(
        history=history,
        projection=projection,
        today_daily=round(today_daily, 2),
        today_site_count=len(active_sites),
    )
