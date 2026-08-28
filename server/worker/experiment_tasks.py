"""Dedicated ARQ worker for isolated GHCR experiments.

This module intentionally has its own queue so experiment runs never consume
site-provisioning workers. Two slots are reserved here: one for the sole active
run and one for cancellation/safety cleanup.
"""
from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

from arq import cron, func
from sqlalchemy import select

from server.config import get_settings
from server.database import async_session
from server.experiment_provisioner import ensure_instance_records, run_experiment
from server.experiment_terraform import MockExperimentTerraform, RealExperimentTerraform
from server.models.audit_log import AuditLog
from server.models.experiment import Experiment, ExperimentEvent, ExperimentStatus
from server.worker.settings import get_redis_settings

logger = logging.getLogger(__name__)
EXPERIMENT_QUEUE_NAME = "arq:experiments"
CLEANABLE_STATUSES = {
    ExperimentStatus.PENDING,
    ExperimentStatus.PROVISIONING,
    ExperimentStatus.RUNNING,
    ExperimentStatus.DESTROYING,
    ExperimentStatus.CLEANUP_FAILED,
}


def _infra():
    if get_settings().use_mock_terraform:
        return MockExperimentTerraform()
    return RealExperimentTerraform()


def _cleanup_deadline(experiment: Experiment) -> datetime:
    created_at = experiment.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    started_at = experiment.started_at
    if started_at is not None and started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=UTC)
    return (started_at or created_at) + timedelta(minutes=experiment.duration_minutes + 60)


async def task_run_experiment(ctx: dict, experiment_id: str) -> None:
    async with async_session() as db:
        experiment = await db.get(Experiment, uuid.UUID(experiment_id))
        if experiment is None:
            logger.error("Experiment %s not found", experiment_id)
            return
        if experiment.status != ExperimentStatus.PENDING:
            logger.warning("Experiment %s is %s; refusing duplicate run", experiment_id, experiment.status.value)
            return
        await run_experiment(db, experiment)


async def task_cleanup_experiment(ctx: dict, experiment_id: str, reason: str = "retry") -> None:
    """Destroy a fleet after revalidating the cleanup reason under a row lock."""
    experiment_uuid = uuid.UUID(experiment_id)
    async with async_session() as db:
        result = await db.execute(
            select(Experiment).where(Experiment.id == experiment_uuid).with_for_update()
        )
        experiment = result.scalar_one_or_none()
        if experiment is None:
            return

        if experiment.status in {ExperimentStatus.COMPLETED, ExperimentStatus.CANCELLED}:
            logger.info("Skipping %s cleanup for terminal experiment %s", reason, experiment.id)
            return
        if reason == "retry" and experiment.status != ExperimentStatus.CLEANUP_FAILED:
            logger.info("Skipping cleanup retry for experiment %s in %s", experiment.id, experiment.status.value)
            return
        if reason == "cancel" and (
            not experiment.cancellation_requested or experiment.status not in CLEANABLE_STATUSES
        ):
            logger.info("Skipping cancellation cleanup for experiment %s", experiment.id)
            return
        if reason == "safety" and (
            experiment.status not in CLEANABLE_STATUSES or _cleanup_deadline(experiment) >= datetime.now(UTC)
        ):
            logger.info("Skipping no-longer-stale cleanup for experiment %s", experiment.id)
            return

        if reason == "cancel":
            experiment.cancellation_requested = True
        experiment.status = ExperimentStatus.DESTROYING
        instances = ensure_instance_records(experiment)
        for instance in instances:
            if instance.get("instance_id"):
                instance["cleanup_status"] = "destroying"
        experiment.instances = instances
        db.add(
            ExperimentEvent(
                experiment_id=experiment.id,
                event_type="cleanup_started",
                payload={"reason": reason},
            )
        )
        await db.commit()

    try:
        await _infra().destroy(experiment_id)
    except Exception as exc:
        async with async_session() as db:
            result = await db.execute(
                select(Experiment).where(Experiment.id == experiment_uuid).with_for_update()
            )
            experiment = result.scalar_one_or_none()
            if experiment is None:
                return
            # A normal run may have completed while this cleanup was waiting on
            # the Terraform lock. Never regress a terminal successful outcome.
            if experiment.status in {ExperimentStatus.COMPLETED, ExperimentStatus.CANCELLED}:
                return
            instances = ensure_instance_records(experiment)
            for instance in instances:
                if instance.get("instance_id"):
                    instance["cleanup_status"] = "failed"
            experiment.instances = instances
            experiment.status = ExperimentStatus.CLEANUP_FAILED
            experiment.cleanup_error = str(exc)[:2000]
            db.add(
                ExperimentEvent(
                    experiment_id=experiment.id,
                    event_type="cleanup_failed",
                    payload={"reason": reason, "error": experiment.cleanup_error},
                )
            )
            await db.commit()
        logger.exception("Experiment %s cleanup failed id=%s", reason, experiment_id)
        return

    async with async_session() as db:
        result = await db.execute(
            select(Experiment).where(Experiment.id == experiment_uuid).with_for_update()
        )
        experiment = result.scalar_one_or_none()
        if experiment is None:
            return
        if experiment.status in {ExperimentStatus.COMPLETED, ExperimentStatus.CANCELLED}:
            return
        instances = ensure_instance_records(experiment)
        for instance in instances:
            if instance.get("instance_id"):
                instance["cleanup_status"] = "destroyed"
                if reason == "cancel" and instance["status"] in {
                    "pending",
                    "provisioning",
                    "starting",
                    "running",
                }:
                    instance["status"] = "cancelled"
                instance["active_pulls"] = 0
        experiment.instances = instances
        experiment.instance_id = None
        experiment.active_pulls = 0
        experiment.destroyed_at = datetime.now(UTC)
        experiment.completed_at = experiment.completed_at or datetime.now(UTC)
        experiment.cleanup_error = None
        if reason == "cancel":
            experiment.status = ExperimentStatus.CANCELLED
            experiment.error_message = "Experiment cancelled; forced fleet cleanup completed"
        else:
            experiment.status = ExperimentStatus.FAILED
            if not experiment.error_message:
                experiment.error_message = "Experiment was cleaned up by the safety janitor"
        db.add(
            ExperimentEvent(
                experiment_id=experiment.id,
                event_type="infrastructure_destroyed",
                payload={"reason": reason},
            )
        )
        db.add(
            AuditLog(
                user_id=experiment.created_by,
                site_id=None,
                action="experiment.destroyed",
                details={"experiment_id": str(experiment.id), "reason": reason},
            )
        )
        await db.commit()


async def cron_cleanup_stale_experiments(ctx: dict) -> None:
    """Clean up experiments whose maximum run window has elapsed."""
    now = datetime.now(UTC)
    async with async_session() as db:
        result = await db.execute(select(Experiment).where(Experiment.status.in_(CLEANABLE_STATUSES)))
        experiments = list(result.scalars().all())

    for experiment in experiments:
        if _cleanup_deadline(experiment) >= now:
            continue
        logger.warning("Safety cleanup for stale experiment %s", experiment.id)
        # task_cleanup_experiment re-reads and revalidates under a lock to avoid
        # racing a run that completed after this snapshot.
        await task_cleanup_experiment(ctx, str(experiment.id), "safety")


class ExperimentWorkerSettings:
    functions = [
        func(task_run_experiment, name="run_ghcr_experiment", timeout=93600, max_tries=1),
        func(task_cleanup_experiment, name="cleanup_ghcr_experiment", timeout=2400, max_tries=1),
    ]
    cron_jobs = [cron(cron_cleanup_stale_experiments, minute=set(range(0, 60, 5)))]
    queue_name = EXPERIMENT_QUEUE_NAME
    job_timeout = 93600
    max_jobs = 2
    max_tries = 1
    redis_settings = get_redis_settings()
