"""Dedicated ARQ worker for isolated GHCR experiments.

This module intentionally has its own queue and concurrency limit so experiment
runs never consume the existing site-provisioning worker pool.
"""
from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

from arq import cron, func
from sqlalchemy import select

from server.config import get_settings
from server.database import async_session
from server.experiment_provisioner import run_experiment
from server.experiment_terraform import MockExperimentTerraform, RealExperimentTerraform
from server.models.audit_log import AuditLog
from server.models.experiment import Experiment, ExperimentStatus
from server.worker.settings import get_redis_settings

logger = logging.getLogger(__name__)
EXPERIMENT_QUEUE_NAME = "arq:experiments"


def _infra():
    if get_settings().use_mock_terraform:
        return MockExperimentTerraform()
    return RealExperimentTerraform()


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


async def task_cleanup_experiment(ctx: dict, experiment_id: str) -> None:
    async with async_session() as db:
        experiment = await db.get(Experiment, uuid.UUID(experiment_id))
        if experiment is None:
            return
        experiment.status = ExperimentStatus.DESTROYING
        await db.commit()
        try:
            await _infra().destroy(str(experiment.id))
        except Exception as exc:
            experiment.status = ExperimentStatus.CLEANUP_FAILED
            experiment.cleanup_error = str(exc)[:2000]
            await db.commit()
            logger.exception("Experiment cleanup retry failed id=%s", experiment.id)
            return
        experiment.instance_id = None
        experiment.destroyed_at = datetime.now(UTC)
        experiment.status = ExperimentStatus.FAILED
        if not experiment.error_message:
            experiment.error_message = "Experiment was cleaned up by the safety janitor"
        db.add(
            AuditLog(
                user_id=experiment.created_by,
                site_id=None,
                action="experiment.destroyed",
                details={"experiment_id": str(experiment.id), "reason": "cleanup"},
            )
        )
        await db.commit()


async def cron_cleanup_stale_experiments(ctx: dict) -> None:
    """Clean up experiments whose maximum run window has elapsed."""
    now = datetime.now(UTC)
    async with async_session() as db:
        result = await db.execute(
            select(Experiment).where(
                Experiment.status.in_(
                    {
                        ExperimentStatus.PENDING,
                        ExperimentStatus.PROVISIONING,
                        ExperimentStatus.RUNNING,
                        ExperimentStatus.DESTROYING,
                        ExperimentStatus.CLEANUP_FAILED,
                    }
                )
            )
        )
        experiments = list(result.scalars().all())

    for experiment in experiments:
        created_at = experiment.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        deadline = created_at + timedelta(minutes=experiment.duration_minutes + 30)
        if deadline >= now:
            continue
        logger.warning("Safety cleanup for stale experiment %s", experiment.id)
        await task_cleanup_experiment(ctx, str(experiment.id))


class ExperimentWorkerSettings:
    functions = [
        func(task_run_experiment, name="run_ghcr_experiment", timeout=5400, max_tries=1),
        func(task_cleanup_experiment, name="cleanup_ghcr_experiment", timeout=900, max_tries=1),
    ]
    cron_jobs = [cron(cron_cleanup_stale_experiments, minute=set(range(0, 60, 5)))]
    queue_name = EXPERIMENT_QUEUE_NAME
    job_timeout = 5400
    max_jobs = 1
    max_tries = 1
    redis_settings = get_redis_settings()
