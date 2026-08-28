from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

from arq import ArqRedis
from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from server.api.deps import DB, AdminUser
from server.config import get_settings
from server.database import async_session
from server.experiment_provisioner import aggregate_instance_progress, ensure_instance_records
from server.experiment_weights import ExperimentWeightError, allocate_weighted_pulls, normalize_weights
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
PROGRESS_EVENT_INTERVAL_SECONDS = 300
MAX_PROGRESS_EVENTS = 1000
MAX_COUNTER_EVENTS = 500


def _validate_progress_callback_token(token: str, experiment_id: uuid.UUID, instance_index: int) -> None:
    try:
        validate_progress_token(token, experiment_id, instance_index)
    except ExperimentError as indexed_error:
        # Rolling-deploy compatibility for pre-fleet single-instance scripts.
        if instance_index != 0:
            raise indexed_error
        validate_progress_token(token, experiment_id)


async def _trim_event_type(
    db,
    experiment_id: uuid.UUID,
    event_type: str,
    keep: int,
) -> None:
    """Hard-cap high-volume telemetry events while retaining lifecycle events."""
    stale_ids = (
        select(ExperimentEvent.id)
        .where(
            ExperimentEvent.experiment_id == experiment_id,
            ExperimentEvent.event_type == event_type,
        )
        .order_by(ExperimentEvent.created_at.desc(), ExperimentEvent.id.desc())
        .offset(keep)
    )
    await db.execute(delete(ExperimentEvent).where(ExperimentEvent.id.in_(stale_ids)))


def set_experiment_arq_pool(pool: ArqRedis) -> None:
    global _experiment_pool
    _experiment_pool = pool


def _get_pool() -> ArqRedis:
    if _experiment_pool is None:
        raise HTTPException(status_code=503, detail="Experiment worker pool not available")
    return _experiment_pool


async def _enqueue_cleanup_job(experiment_id: uuid.UUID, reason: str) -> None:
    # Do not use a stable job ID: an earlier completed/failed ARQ job with that
    # ID would make a genuine retry return None without queuing any work.
    job = await _get_pool().enqueue_job(
        "cleanup_ghcr_experiment",
        str(experiment_id),
        reason,
        _queue_name="arq:experiments",
    )
    if job is None:
        raise RuntimeError("Experiment cleanup job was not enqueued")


class ExperimentCreateRequest(BaseModel):
    target_refs: list[str] = Field(min_length=1, max_length=4)
    resolved_target_refs: list[str] = Field(min_length=1, max_length=4)
    target_weights: list[int] = Field(default_factory=list, max_length=4)
    rate_per_minute: int = Field(ge=1)
    duration_minutes: int = Field(ge=1)
    concurrency_limit: int = Field(ge=1)
    instance_count: int = Field(default=1, ge=1)
    confirmation: str


class ExperimentPreflightRequest(BaseModel):
    target_refs: list[str] = Field(min_length=1, max_length=4)
    # Per-instance expectation; fleet transfer is derived with instance_count.
    expected_pulls: int = Field(ge=1)
    instance_count: int = Field(default=1, ge=1)
    target_weights: list[int] = Field(default_factory=list, max_length=4)


class ExperimentTargetResponse(BaseModel):
    requested_ref: str
    target_ref: str
    package_url: str
    platform: str
    image_size_bytes: int
    layer_count: int
    weight: int = 1
    expected_pulls: int
    estimated_transfer_bytes: int = 0
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


class ExperimentInstanceResponse(BaseModel):
    index: int
    instance_id: str | None = None
    status: str
    cleanup_status: str
    launched_pulls: int = 0
    successful_pulls: int = 0
    failed_pulls: int = 0
    active_pulls: int = 0
    max_concurrency: int = 0
    last_progress_at: datetime | None = None
    error_message: str | None = None
    run_log: str | None = None
    targets: list[dict] = Field(default_factory=list)


class ExperimentConfigResponse(BaseModel):
    enabled: bool
    target_ref: str
    target_name: str | None
    max_rate_per_minute: int
    max_duration_minutes: int
    max_concurrency: int
    max_instances: int
    max_images: int
    max_transfer_bytes: int


class TargetProgressRequest(BaseModel):
    target_ref: str
    launched: int = Field(ge=0)
    successful: int = Field(ge=0)
    failed: int = Field(ge=0)
    active: int = Field(ge=0)


class ExperimentProgressRequest(BaseModel):
    instance_index: int = Field(default=0, ge=0)
    launched: int = Field(ge=0)
    successful: int = Field(ge=0)
    failed: int = Field(ge=0)
    active: int = Field(ge=0)
    max_concurrency: int = Field(ge=0)
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
    instance_count: int
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
    instances: list[ExperimentInstanceResponse]
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
        max_instances=getattr(settings, "ghcr_experiment_max_instances", 10),
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
    if body.instance_count > getattr(settings, "ghcr_experiment_max_instances", 10):
        raise HTTPException(status_code=400, detail="Too many experiment instances")
    try:
        weights = normalize_weights(len(target_refs), body.target_weights)
        results = await asyncio.gather(*(preflight_image(target) for target in target_refs))
    except (ExperimentError, ExperimentWeightError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    allocations = allocate_weighted_pulls(body.expected_pulls, weights)
    targets = [
        ExperimentTargetResponse(
            requested_ref=result.requested_ref,
            target_ref=result.target_ref,
            package_url=result.package_url,
            platform=result.platform,
            image_size_bytes=result.image_size_bytes,
            layer_count=result.layer_count,
            weight=weights[index],
            expected_pulls=allocations[index] * body.instance_count,
            estimated_transfer_bytes=(
                result.image_size_bytes * allocations[index] * body.instance_count
            ),
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
    expected_pulls = body.rate_per_minute * body.duration_minutes * body.instance_count
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
            instance_count=body.instance_count,
            target_weights=body.target_weights,
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
        instances = ensure_instance_records(experiment)
        for instance in instances:
            instance["status"] = "failed"
            instance["error_message"] = experiment.error_message
        experiment.instances = instances
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
async def get_experiment_events(
    experiment_id: uuid.UUID,
    db: DB,
    admin: AdminUser,
    limit: int = Query(default=200, ge=1, le=500),
):
    try:
        await get_experiment(db, experiment_id)
    except ExperimentError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    result = await db.execute(
        select(ExperimentEvent)
        .where(ExperimentEvent.experiment_id == experiment_id)
        .order_by(ExperimentEvent.created_at.desc(), ExperimentEvent.id.desc())
        .limit(limit)
    )
    # Keep the existing chronological rendering contract while bounding the
    # response to the newest page.
    events = list(reversed(result.scalars().all()))
    return [ExperimentEventResponse.model_validate(item) for item in events]


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
        locked = await db.execute(
            select(Experiment)
            .where(Experiment.id == experiment_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        experiment = locked.scalar_one()
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
        await db.flush()
        await _trim_event_type(db, experiment.id, "counter_snapshot", MAX_COUNTER_EVENTS)
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
    token = authorization.removeprefix("Bearer ")
    try:
        _validate_progress_callback_token(token, experiment_id, body.instance_index)
    except ExperimentError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    # Serialize callbacks from fleet members by locking the parent row. This
    # prevents concurrent JSON snapshots from losing another member's update.
    result = await db.execute(
        select(Experiment).where(Experiment.id == experiment_id).with_for_update()
    )
    experiment = result.scalar_one_or_none()
    if experiment is None:
        raise HTTPException(status_code=404, detail="Experiment not found")
    if experiment.status != ExperimentStatus.RUNNING:
        raise HTTPException(status_code=409, detail="Experiment is not running")
    if body.instance_index >= experiment.instance_count:
        raise HTTPException(status_code=400, detail="Unknown fleet instance")
    expected_per_instance = experiment.rate_per_minute * experiment.duration_minutes
    if body.launched > expected_per_instance:
        raise HTTPException(status_code=400, detail="Progress exceeds per-instance pull count")
    if body.successful + body.failed + body.active != body.launched:
        raise HTTPException(status_code=400, detail="Invalid progress totals")
    if body.active > experiment.concurrency_limit or body.max_concurrency > experiment.concurrency_limit:
        raise HTTPException(status_code=400, detail="Progress exceeds the per-instance concurrency limit")

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
    weights = normalize_weights(
        len(experiment.targets),
        [int(item.get("weight", 1)) for item in experiment.targets],
    )
    quotas = allocate_weighted_pulls(expected_per_instance, weights)
    quota_by_ref = {
        target["target_ref"]: quota
        for target, quota in zip(experiment.targets, quotas, strict=True)
    }
    for progress in body.targets:
        if progress.launched > quota_by_ref[progress.target_ref]:
            raise HTTPException(status_code=400, detail="Target progress exceeds its weighted quota")
        if progress.successful + progress.failed + progress.active != progress.launched:
            raise HTTPException(status_code=400, detail="Invalid per-target progress totals")

    instances = ensure_instance_records(experiment)
    instance = instances[body.instance_index]
    if (
        body.launched < int(instance.get("launched_pulls", 0))
        or body.successful < int(instance.get("successful_pulls", 0))
        or body.failed < int(instance.get("failed_pulls", 0))
    ):
        raise HTTPException(status_code=409, detail="Instance progress must be monotonic")
    prior_targets = {item["target_ref"]: item for item in instance.get("targets", [])}
    for progress in body.targets:
        previous = prior_targets.get(progress.target_ref, {})
        if (
            progress.launched < int(previous.get("launched", 0))
            or progress.successful < int(previous.get("successful", 0))
            or progress.failed < int(previous.get("failed", 0))
        ):
            raise HTTPException(status_code=409, detail="Target progress must be monotonic")
    now = datetime.now(UTC)
    previous_event_at = instance.get("last_progress_event_at")
    parsed_event_at = datetime.fromisoformat(previous_event_at) if previous_event_at else None
    should_record_progress = (
        parsed_event_at is None
        or (now - parsed_event_at).total_seconds() >= PROGRESS_EVENT_INTERVAL_SECONDS
        or (body.launched == expected_per_instance and body.active == 0)
    )
    instance.update(
        {
            "status": "running",
            "launched_pulls": body.launched,
            "successful_pulls": body.successful,
            "failed_pulls": body.failed,
            "active_pulls": body.active,
            "max_concurrency": max(int(instance.get("max_concurrency", 0)), body.max_concurrency),
            "last_progress_at": now.isoformat(),
            "targets": [item.model_dump() for item in body.targets],
        }
    )
    if should_record_progress:
        instance["last_progress_event_at"] = now.isoformat()
    aggregate_instance_progress(experiment, instances)
    experiment.last_progress_at = now
    should_refresh_counts = (
        experiment.last_counter_poll_at is None
        or (now - experiment.last_counter_poll_at).total_seconds() >= PROGRESS_EVENT_INTERVAL_SECONDS
    )
    if should_refresh_counts:
        experiment.last_counter_poll_at = now
    if should_record_progress:
        db.add(
            ExperimentEvent(
                experiment_id=experiment.id,
                event_type="progress",
                payload={
                    **body.model_dump(),
                    "instance_id": instances[body.instance_index].get("instance_id"),
                },
            )
        )
        await db.flush()
        await _trim_event_type(db, experiment.id, "progress", MAX_PROGRESS_EVENTS)
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
        instances = ensure_instance_records(experiment)
        for instance in instances:
            instance["status"] = "cancelled"
        experiment.instances = instances
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

    instances = ensure_instance_records(experiment)
    instance_ids = [item["instance_id"] for item in instances if item.get("instance_id")]
    failures = list(instance_ids)
    if instance_ids:
        settings = get_settings()
        remote = MockSSM() if settings.use_mock_ssm else RealSSM()
        command = (
            "mkdir -p /tmp/flare-ghcr-experiment; "
            "touch /tmp/flare-ghcr-experiment/CANCELLED; "
            "pkill -x crane || true"
        )
        for attempt in range(3):
            signal_results = await asyncio.gather(
                *(remote.run_command(instance_id, command, timeout_seconds=20) for instance_id in failures),
                return_exceptions=True,
            )
            failures = [
                instance_id
                for instance_id, result in zip(failures, signal_results, strict=True)
                if isinstance(result, BaseException) or result.status != "success"
            ]
            if not failures:
                break
            if attempt < 2:
                await asyncio.sleep(2**attempt)

        if failures:
            instances = ensure_instance_records(experiment)
            for instance in instances:
                if instance.get("instance_id") in failures:
                    instance["error_message"] = "Cancellation signal failed; forced cleanup queued"
            experiment.instances = instances
            db.add(
                ExperimentEvent(
                    experiment_id=experiment.id,
                    event_type="cancellation_signal_failed",
                    payload={"instance_ids": failures, "attempts": 3},
                )
            )
            await db.commit()
            enqueue_error: Exception | None = None
            try:
                await _enqueue_cleanup_job(experiment.id, "cancel")
            except Exception as exc:
                enqueue_error = exc
            detail = (
                "Cancellation was recorded, but some EC2 signals failed. "
                "Forced fleet cleanup was queued; retry cancellation if the fleet remains active."
            )
            if enqueue_error is not None:
                detail += f" Cleanup could not be queued: {enqueue_error}"
            raise HTTPException(status_code=502, detail=detail)

        instances = ensure_instance_records(experiment)
        for instance in instances:
            if instance.get("error_message") == "Cancellation signal failed; forced cleanup queued":
                instance["error_message"] = None
        experiment.instances = instances
        db.add(
            ExperimentEvent(
                experiment_id=experiment.id,
                event_type="cancellation_signalled",
                payload={"instance_ids": instance_ids},
            )
        )
        await db.commit()
    elif experiment.status == ExperimentStatus.PROVISIONING:
        # Terraform may be hung after creating resources but before returning
        # outputs. A second worker slot can wait for/clear the state lock and
        # destroy whatever exists in the experiment state.
        try:
            await _enqueue_cleanup_job(experiment.id, "cancel")
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Cancellation recorded but forced cleanup could not be queued: {exc}",
            ) from exc

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
    try:
        await _enqueue_cleanup_job(experiment.id, "retry")
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Cleanup could not be queued: {exc}") from exc
    return ExperimentResponse.model_validate(experiment)
