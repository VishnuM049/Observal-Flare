from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

from arq import ArqRedis
from fastapi import APIRouter, BackgroundTasks, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from server.api.deps import DB, AdminUser
from server.config import get_settings
from server.database import async_session
from server.mock import MockSSM
from server.models.audit_log import AuditLog
from server.models.experiment import Experiment, ExperimentEvent, ExperimentStatus
from server.services.experiment_service import (
    ExperimentError,
    create_experiment,
    get_experiment,
    list_experiments,
    preflight_image,
    read_download_count,
    validate_progress_token,
    validate_target_ref,
)
from server.ssm import RealSSM

router = APIRouter(prefix="/api/experiments", tags=["experiments"])
_experiment_pool: ArqRedis | None = None


def set_experiment_arq_pool(pool: ArqRedis) -> None:
    global _experiment_pool
    _experiment_pool = pool


def _get_pool() -> ArqRedis:
    if _experiment_pool is None:
        raise HTTPException(status_code=503, detail="Experiment worker pool not available")
    return _experiment_pool


class ExperimentCreateRequest(BaseModel):
    target_refs: list[str] = Field(min_length=1, max_length=4)
    resolved_target_refs: list[str] = Field(min_length=1, max_length=4)
    rate_per_minute: int = Field(ge=1)
    duration_minutes: int = Field(ge=1)
    concurrency_limit: int = Field(ge=1)
    confirmation: str


class ExperimentPreflightRequest(BaseModel):
    target_refs: list[str] = Field(min_length=1, max_length=4)
    expected_pulls: int = Field(ge=1)


class ExperimentTargetResponse(BaseModel):
    requested_ref: str
    target_ref: str
    package_url: str
    platform: str
    image_size_bytes: int
    layer_count: int
    expected_pulls: int
    launched_pulls: int = 0
    successful_pulls: int = 0
    failed_pulls: int = 0
    baseline_count: int | None = None
    current_count: int | None = None
    final_count: int | None = None


class ExperimentPreflightResponse(BaseModel):
    targets: list[ExperimentTargetResponse]
    estimated_transfer_bytes: int
    within_transfer_limit: bool
    max_transfer_bytes: int


class ExperimentConfigResponse(BaseModel):
    enabled: bool
    target_ref: str
    target_name: str | None
    max_rate_per_minute: int
    max_duration_minutes: int
    max_concurrency: int
    max_images: int
    max_transfer_bytes: int


class TargetProgressRequest(BaseModel):
    target_ref: str
    launched: int = Field(ge=0)
    successful: int = Field(ge=0)
    failed: int = Field(ge=0)
    active: int = Field(ge=0, le=1)


class ExperimentProgressRequest(BaseModel):
    launched: int = Field(ge=0)
    successful: int = Field(ge=0)
    failed: int = Field(ge=0)
    active: int = Field(ge=0, le=4)
    max_concurrency: int = Field(ge=0, le=4)
    elapsed_seconds: float = Field(ge=0)
    targets: list[TargetProgressRequest]


class ExperimentEventResponse(BaseModel):
    id: uuid.UUID
    event_type: str
    payload: dict
    created_at: datetime

    class Config:
        from_attributes = True


class ExperimentResponse(BaseModel):
    id: uuid.UUID
    status: ExperimentStatus
    target_ref: str
    package_url: str
    targets: list[ExperimentTargetResponse]
    rate_per_minute: int
    duration_minutes: int
    expected_pulls: int
    concurrency_limit: int
    platform: str
    image_size_bytes: int
    layer_count: int
    estimated_transfer_bytes: int
    instance_type: str
    launched_pulls: int
    successful_pulls: int
    failed_pulls: int
    active_pulls: int
    max_concurrency: int | None
    baseline_count: int | None
    immediate_count: int | None
    delayed_count: int | None
    results: dict
    instance_id: str | None
    terraform_state_key: str
    cancellation_requested: bool
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    destroyed_at: datetime | None
    last_progress_at: datetime | None
    error_message: str | None
    cleanup_error: str | None

    class Config:
        from_attributes = True


@router.get("/config", response_model=ExperimentConfigResponse)
async def get_experiment_config(admin: AdminUser):
    settings = get_settings()
    target_name = None
    if settings.ghcr_experiment_image:
        match = validate_target_ref(settings.ghcr_experiment_image)
        target_name = f"{match.group('namespace')}/{match.group('package')}"
    return ExperimentConfigResponse(
        enabled=settings.ghcr_experiments_enabled,
        target_ref=settings.ghcr_experiment_image,
        target_name=target_name,
        max_rate_per_minute=settings.ghcr_experiment_max_rate_per_minute,
        max_duration_minutes=settings.ghcr_experiment_max_duration_minutes,
        max_concurrency=settings.ghcr_experiment_max_concurrency,
        max_images=settings.ghcr_experiment_max_images,
        max_transfer_bytes=settings.ghcr_experiment_max_transfer_gb * 1_000_000_000,
    )


@router.post("/preflight", response_model=ExperimentPreflightResponse)
async def preflight_experiment(body: ExperimentPreflightRequest, admin: AdminUser):
    settings = get_settings()
    if not settings.ghcr_experiments_enabled:
        raise HTTPException(status_code=400, detail="GHCR experiments are disabled")
    target_refs = [target.strip() for target in body.target_refs]
    if len(set(target_refs)) != len(target_refs):
        raise HTTPException(status_code=400, detail="Experiment images must be unique")
    if len(target_refs) > settings.ghcr_experiment_max_images:
        raise HTTPException(status_code=400, detail="Too many experiment images")
    try:
        results = await asyncio.gather(*(preflight_image(target) for target in target_refs))
    except ExperimentError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    per_target, remainder = divmod(body.expected_pulls, len(results))
    targets = [
        ExperimentTargetResponse(
            requested_ref=result.requested_ref,
            target_ref=result.target_ref,
            package_url=result.package_url,
            platform=result.platform,
            image_size_bytes=result.image_size_bytes,
            layer_count=result.layer_count,
            expected_pulls=per_target + (1 if index < remainder else 0),
        )
        for index, result in enumerate(results)
    ]
    estimated_transfer_bytes = sum(item.image_size_bytes * item.expected_pulls for item in targets)
    max_transfer_bytes = settings.ghcr_experiment_max_transfer_gb * 1_000_000_000
    return ExperimentPreflightResponse(
        targets=targets,
        estimated_transfer_bytes=estimated_transfer_bytes,
        within_transfer_limit=estimated_transfer_bytes <= max_transfer_bytes,
        max_transfer_bytes=max_transfer_bytes,
    )


@router.get("", response_model=list[ExperimentResponse])
async def list_all_experiments(db: DB, admin: AdminUser):
    return [ExperimentResponse.model_validate(item) for item in await list_experiments(db)]


@router.post("", response_model=ExperimentResponse, status_code=201)
async def create_new_experiment(body: ExperimentCreateRequest, db: DB, admin: AdminUser):
    expected_pulls = body.rate_per_minute * body.duration_minutes
    if body.confirmation != f"RUN {expected_pulls}":
        raise HTTPException(status_code=400, detail=f'Type "RUN {expected_pulls}" to confirm')
    try:
        experiment = await create_experiment(
            db,
            user=admin,
            target_refs=body.target_refs,
            expected_resolved_refs=body.resolved_target_refs,
            rate_per_minute=body.rate_per_minute,
            duration_minutes=body.duration_minutes,
            concurrency_limit=body.concurrency_limit,
        )
    except ExperimentError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        job = await _get_pool().enqueue_job(
            "run_ghcr_experiment",
            str(experiment.id),
            _job_id=f"ghcr-experiment:{experiment.id}",
            _queue_name="arq:experiments",
        )
        if job is None:
            raise RuntimeError("Experiment job was not enqueued")
    except Exception as exc:
        experiment.status = ExperimentStatus.FAILED
        experiment.error_message = f"Could not enqueue experiment: {exc}"[:2000]
        await db.commit()
        raise HTTPException(status_code=503, detail=experiment.error_message) from exc
    return ExperimentResponse.model_validate(experiment)


@router.get("/{experiment_id}", response_model=ExperimentResponse)
async def get_experiment_detail(experiment_id: uuid.UUID, db: DB, admin: AdminUser):
    try:
        experiment = await get_experiment(db, experiment_id)
    except ExperimentError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ExperimentResponse.model_validate(experiment)


@router.get("/{experiment_id}/events", response_model=list[ExperimentEventResponse])
async def get_experiment_events(experiment_id: uuid.UUID, db: DB, admin: AdminUser):
    try:
        await get_experiment(db, experiment_id)
    except ExperimentError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    result = await db.execute(
        select(ExperimentEvent)
        .where(ExperimentEvent.experiment_id == experiment_id)
        .order_by(ExperimentEvent.created_at.asc())
    )
    return [ExperimentEventResponse.model_validate(item) for item in result.scalars().all()]


async def _refresh_current_counts(experiment_id: uuid.UUID) -> None:
    async with async_session() as db:
        experiment = await db.get(Experiment, experiment_id)
        if experiment is None:
            return
        targets = [dict(item) for item in experiment.targets]
        values = await asyncio.gather(
            *(read_download_count(item["package_url"]) for item in targets),
            return_exceptions=True,
        )
        await db.refresh(experiment)
        latest_targets = {item["target_ref"]: dict(item) for item in experiment.targets}
        snapshot = []
        for target, value in zip(targets, values, strict=True):
            current_target = latest_targets[target["target_ref"]]
            if isinstance(value, Exception):
                snapshot.append({"target_ref": target["target_ref"], "error": str(value)[:200]})
                continue
            current_target["current_count"] = value
            snapshot.append({"target_ref": target["target_ref"], "count": value})
        experiment.targets = list(latest_targets.values())
        db.add(
            ExperimentEvent(
                experiment_id=experiment.id,
                event_type="counter_snapshot",
                payload={"targets": snapshot},
            )
        )
        await db.commit()


@router.post("/{experiment_id}/progress", status_code=204)
async def report_experiment_progress(
    experiment_id: uuid.UUID,
    body: ExperimentProgressRequest,
    db: DB,
    background_tasks: BackgroundTasks,
    authorization: str | None = Header(None),
):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing progress token")
    try:
        validate_progress_token(authorization.removeprefix("Bearer "), experiment_id)
        experiment = await get_experiment(db, experiment_id)
    except ExperimentError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    if experiment.status != ExperimentStatus.RUNNING:
        raise HTTPException(status_code=409, detail="Experiment is not running")
    if body.launched > experiment.expected_pulls:
        raise HTTPException(status_code=400, detail="Progress exceeds expected pull count")
    if body.successful + body.failed + body.active > body.launched:
        raise HTTPException(status_code=400, detail="Invalid progress totals")
    if (
        body.launched < experiment.launched_pulls
        or body.successful < experiment.successful_pulls
        or body.failed < experiment.failed_pulls
    ):
        raise HTTPException(status_code=409, detail="Progress must be monotonic")

    if (
        sum(item.launched for item in body.targets) != body.launched
        or sum(item.successful for item in body.targets) != body.successful
        or sum(item.failed for item in body.targets) != body.failed
        or sum(item.active for item in body.targets) != body.active
    ):
        raise HTTPException(status_code=400, detail="Per-target progress does not match aggregate progress")
    configured_refs = {item["target_ref"] for item in experiment.targets}
    reported_refs = {item.target_ref for item in body.targets}
    if reported_refs != configured_refs or len(body.targets) != len(configured_refs):
        raise HTTPException(status_code=400, detail="Progress targets do not match experiment targets")

    current_targets = {item["target_ref"]: dict(item) for item in experiment.targets}
    for progress in body.targets:
        target = current_targets[progress.target_ref]
        if (
            progress.launched < target["launched_pulls"]
            or progress.successful < target["successful_pulls"]
            or progress.failed < target["failed_pulls"]
        ):
            raise HTTPException(status_code=409, detail="Target progress must be monotonic")
        target["launched_pulls"] = progress.launched
        target["successful_pulls"] = progress.successful
        target["failed_pulls"] = progress.failed
    experiment.targets = list(current_targets.values())
    experiment.launched_pulls = body.launched
    experiment.successful_pulls = body.successful
    experiment.failed_pulls = body.failed
    experiment.active_pulls = body.active
    experiment.max_concurrency = max(experiment.max_concurrency or 0, body.max_concurrency)
    now = datetime.now(UTC)
    experiment.last_progress_at = now
    should_refresh_counts = (
        experiment.last_counter_poll_at is None
        or (now - experiment.last_counter_poll_at).total_seconds() >= 30
    )
    if should_refresh_counts:
        experiment.last_counter_poll_at = now
    db.add(
        ExperimentEvent(
            experiment_id=experiment.id,
            event_type="progress",
            payload=body.model_dump(),
        )
    )
    await db.commit()
    if should_refresh_counts:
        background_tasks.add_task(_refresh_current_counts, experiment.id)


@router.post("/{experiment_id}/cancel", response_model=ExperimentResponse)
async def cancel_experiment(experiment_id: uuid.UUID, db: DB, admin: AdminUser):
    try:
        experiment = await get_experiment(db, experiment_id)
    except ExperimentError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if experiment.status not in {
        ExperimentStatus.PENDING,
        ExperimentStatus.PROVISIONING,
        ExperimentStatus.RUNNING,
    }:
        raise HTTPException(status_code=400, detail="Experiment is not cancellable")

    experiment.cancellation_requested = True
    if experiment.status == ExperimentStatus.PENDING and experiment.instance_id is None:
        experiment.status = ExperimentStatus.CANCELLED
        experiment.completed_at = datetime.now(UTC)
    db.add(
        ExperimentEvent(
            experiment_id=experiment.id,
            event_type="cancellation_requested",
            payload={"requested_by": str(admin.id)},
        )
    )
    db.add(
        AuditLog(
            user_id=admin.id,
            site_id=None,
            action="experiment.cancel_requested",
            details={"experiment_id": str(experiment.id)},
        )
    )
    await db.commit()

    if experiment.instance_id and experiment.status == ExperimentStatus.RUNNING:
        settings = get_settings()
        remote = MockSSM() if settings.use_mock_ssm else RealSSM()
        command = (
            "mkdir -p /tmp/flare-ghcr-experiment; "
            "touch /tmp/flare-ghcr-experiment/CANCELLED; "
            "pkill -x crane || true"
        )
        result = await remote.run_command(experiment.instance_id, command, timeout_seconds=60)
        if result.status != "success":
            raise HTTPException(status_code=502, detail="Cancellation was recorded but the EC2 signal failed")

    return ExperimentResponse.model_validate(experiment)


@router.post("/{experiment_id}/retry-cleanup", response_model=ExperimentResponse)
async def retry_experiment_cleanup(experiment_id: uuid.UUID, db: DB, admin: AdminUser):
    try:
        experiment = await get_experiment(db, experiment_id)
    except ExperimentError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if experiment.status != ExperimentStatus.CLEANUP_FAILED:
        raise HTTPException(status_code=400, detail="Experiment is not awaiting cleanup")
    db.add(
        AuditLog(
            user_id=admin.id,
            site_id=None,
            action="experiment.cleanup_requested",
            details={"experiment_id": str(experiment.id)},
        )
    )
    await db.commit()
    await _get_pool().enqueue_job(
        "cleanup_ghcr_experiment",
        str(experiment.id),
        _queue_name="arq:experiments",
    )
    return ExperimentResponse.model_validate(experiment)
