from __future__ import annotations

import asyncio
import json
import logging
import shlex
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from server.compute import AWSCompute, ComputeRunner, MockCompute
from server.config import get_settings
from server.experiment_script import RESULT_MARKER, build_experiment_script
from server.experiment_terraform import (
    ExperimentInfraRunner,
    MockExperimentTerraform,
    RealExperimentTerraform,
)
from server.models.audit_log import AuditLog
from server.models.experiment import Experiment, ExperimentEvent, ExperimentStatus
from server.services.experiment_service import create_progress_token, read_download_count
from server.ssm import CommandResult, RealSSM, SSMRunner

logger = logging.getLogger(__name__)
CountReader = Callable[[str], Awaitable[int]]
Sleep = Callable[[float], Awaitable[None]]


class ExperimentCancelledError(Exception):
    pass


class MockExperimentSSM(SSMRunner):
    async def run_command(self, instance_id: str, script: str, timeout_seconds: int = 600) -> CommandResult:
        requested_line = next(line for line in script.splitlines() if line.startswith("REQUESTED="))
        requested = int(requested_line.split("=", 1)[1])
        targets_line = next(line for line in script.splitlines() if line.startswith("TARGETS_JSON="))
        target_refs = json.loads(shlex.split(targets_line.split("=", 1)[1])[0])
        per_target, remainder = divmod(requested, len(target_refs))
        result = {
            "requested": requested,
            "launched": requested,
            "successful": requested,
            "failed": 0,
            "max_concurrency": min(requested, 3),
            "interval_seconds": 1.25,
            "started_at": datetime.now(UTC).isoformat(),
            "completed_at": datetime.now(UTC).isoformat(),
            "stop_reason": None,
            "targets": [
                {
                    "target_ref": target_ref,
                    "launched": per_target + (1 if index < remainder else 0),
                    "successful": per_target + (1 if index < remainder else 0),
                    "failed": 0,
                }
                for index, target_ref in enumerate(target_refs)
            ],
        }
        return CommandResult(status="success", output=RESULT_MARKER + json.dumps(result))


def _defaults() -> tuple[ExperimentInfraRunner, SSMRunner, ComputeRunner, CountReader]:
    settings = get_settings()
    if settings.is_local:
        async def mock_count(_: str) -> int:
            return 100

        return MockExperimentTerraform(), MockExperimentSSM(), MockCompute(), mock_count
    return RealExperimentTerraform(), RealSSM(), AWSCompute(), read_download_count


def _parse_result(output: str) -> dict:
    result_lines = [line for line in output.splitlines() if line.startswith(RESULT_MARKER)]
    if len(result_lines) != 1:
        raise RuntimeError("Experiment command did not return exactly one result summary")
    try:
        result = json.loads(result_lines[0][len(RESULT_MARKER):])
    except json.JSONDecodeError as exc:
        raise RuntimeError("Experiment command returned invalid result JSON") from exc
    required = {"requested", "launched", "successful", "failed", "max_concurrency"}
    if not required.issubset(result):
        raise RuntimeError("Experiment result summary is incomplete")
    return result


async def _update_target_counts(
    experiment: Experiment,
    count_reader: CountReader,
    field: str,
) -> int:
    targets = [dict(item) for item in experiment.targets]
    counts = await asyncio.gather(*(count_reader(item["package_url"]) for item in targets))
    for target, count in zip(targets, counts, strict=True):
        target[field] = count
    experiment.targets = targets
    return sum(counts)


def _add_event(db: AsyncSession, experiment: Experiment, event_type: str, **payload: object) -> None:
    db.add(ExperimentEvent(experiment_id=experiment.id, event_type=event_type, payload=payload))


def _add_audit(db: AsyncSession, experiment: Experiment, action: str, **extra: object) -> None:
    details: dict[str, object] = {"experiment_id": str(experiment.id), "status": experiment.status.value}
    details.update(extra)
    db.add(
        AuditLog(
            user_id=experiment.created_by,
            site_id=None,
            action=action,
            details=details,
        )
    )


async def run_experiment(
    db: AsyncSession,
    experiment: Experiment,
    *,
    infra: ExperimentInfraRunner | None = None,
    remote: SSMRunner | None = None,
    compute: ComputeRunner | None = None,
    count_reader: CountReader | None = None,
    sleep: Sleep = asyncio.sleep,
) -> Experiment:
    default_infra, default_remote, default_compute, default_count_reader = _defaults()
    infra = infra or default_infra
    remote = remote or default_remote
    compute = compute or default_compute
    count_reader = count_reader or default_count_reader
    run_error: Exception | None = None
    cancelled = False
    experiment_id = experiment.id

    try:
        await db.refresh(experiment)
        if experiment.cancellation_requested:
            raise ExperimentCancelledError("Experiment cancelled by administrator")
        experiment.status = ExperimentStatus.PROVISIONING
        _add_event(db, experiment, "provisioning")
        experiment.baseline_count = await _update_target_counts(
            experiment,
            count_reader,
            "baseline_count",
        )
        _add_event(db, experiment, "baseline_recorded", count=experiment.baseline_count)
        await db.commit()

        safety_minutes = experiment.duration_minutes + 15
        expires_at = datetime.now(UTC) + timedelta(minutes=safety_minutes)
        result = await infra.apply(
            str(experiment.id),
            experiment.instance_type,
            expires_at.isoformat(),
            safety_minutes,
        )
        experiment.instance_id = result.instance_id
        _add_event(db, experiment, "infrastructure_provisioned", instance_id=result.instance_id)
        await db.commit()
        await db.refresh(experiment)
        if experiment.cancellation_requested:
            raise ExperimentCancelledError("Experiment cancelled by administrator")

        await compute.start(result.instance_id, timeout_seconds=300)
        await db.refresh(experiment)
        if experiment.cancellation_requested:
            raise ExperimentCancelledError("Experiment cancelled by administrator")
        experiment.status = ExperimentStatus.RUNNING
        experiment.started_at = datetime.now(UTC)
        _add_event(db, experiment, "running", expected_pulls=experiment.expected_pulls)
        _add_audit(db, experiment, "experiment.started", instance_id=result.instance_id)
        await db.commit()

        settings = get_settings()
        progress_url = f"{settings.flare_base_url.rstrip('/')}/api/experiments/{experiment.id}/progress"
        script = build_experiment_script(
            [item["target_ref"] for item in experiment.targets],
            experiment.rate_per_minute,
            experiment.expected_pulls,
            experiment.concurrency_limit,
            progress_url,
            create_progress_token(experiment.id),
        )
        command_timeout = experiment.duration_minutes * 60 + 600
        await db.refresh(experiment)
        if experiment.cancellation_requested:
            raise ExperimentCancelledError("Experiment cancelled by administrator")
        command_result = await remote.run_command(result.instance_id, script, timeout_seconds=command_timeout)
        experiment.run_log = command_result.output[-2000:]
        await db.refresh(experiment)
        if experiment.cancellation_requested:
            raise ExperimentCancelledError("Experiment cancelled by administrator")

        summary = _parse_result(command_result.output)
        experiment.results = summary
        experiment.launched_pulls = int(summary["launched"])
        experiment.successful_pulls = int(summary["successful"])
        experiment.failed_pulls = int(summary["failed"])
        experiment.active_pulls = 0
        experiment.max_concurrency = int(summary["max_concurrency"])
        target_results = {item["target_ref"]: item for item in summary.get("targets", [])}
        targets = [dict(item) for item in experiment.targets]
        for target in targets:
            target.update(target_results.get(target["target_ref"], {}))
            target["launched_pulls"] = target.pop("launched", target["launched_pulls"])
            target["successful_pulls"] = target.pop("successful", target["successful_pulls"])
            target["failed_pulls"] = target.pop("failed", target["failed_pulls"])
        experiment.targets = targets
        _add_event(db, experiment, "run_completed", **summary)
        await db.commit()

        if command_result.status != "success":
            raise RuntimeError(f"Experiment command failed: {summary.get('stop_reason') or command_result.status}")
        if experiment.successful_pulls != experiment.expected_pulls:
            raise RuntimeError(
                f"Experiment completed {experiment.successful_pulls}/{experiment.expected_pulls} pulls"
            )

        await sleep(10)
        experiment.immediate_count = await _update_target_counts(
            experiment,
            count_reader,
            "current_count",
        )
        _add_event(db, experiment, "immediate_count_recorded", count=experiment.immediate_count)
        await db.commit()
    except Exception as exc:
        logger.exception("GHCR experiment failed id=%s", experiment_id)
        run_error = exc
        cancelled = isinstance(exc, ExperimentCancelledError)
        await db.rollback()
        reloaded = await db.get(Experiment, experiment_id)
        if reloaded is None:
            raise RuntimeError(f"Experiment {experiment_id} disappeared during failure handling") from exc
        experiment = reloaded
        experiment.error_message = str(exc)[:2000]
        await db.commit()
    finally:
        experiment.status = ExperimentStatus.DESTROYING
        experiment.active_pulls = 0
        _add_event(db, experiment, "destroying")
        await db.commit()
        try:
            await infra.destroy(str(experiment.id))
        except Exception as cleanup_exc:
            logger.exception("GHCR experiment cleanup failed id=%s", experiment.id)
            experiment.status = ExperimentStatus.CLEANUP_FAILED
            experiment.cleanup_error = str(cleanup_exc)[:2000]
            _add_event(db, experiment, "cleanup_failed", error=experiment.cleanup_error)
            _add_audit(db, experiment, "experiment.cleanup_failed", error=experiment.cleanup_error)
            await db.commit()
            return experiment

        experiment.instance_id = None
        experiment.destroyed_at = datetime.now(UTC)
        _add_event(db, experiment, "infrastructure_destroyed")
        await db.commit()

    if run_error is None:
        await sleep(90)
        try:
            experiment.delayed_count = await _update_target_counts(
                experiment,
                count_reader,
                "final_count",
            )
            experiment.status = ExperimentStatus.COMPLETED
            experiment.completed_at = datetime.now(UTC)
            _add_event(db, experiment, "delayed_count_recorded", count=experiment.delayed_count)
            _add_audit(
                db,
                experiment,
                "experiment.completed",
                successful_pulls=experiment.successful_pulls,
                baseline_count=experiment.baseline_count,
                delayed_count=experiment.delayed_count,
            )
        except Exception as exc:
            run_error = exc
            experiment.error_message = f"Delayed counter read failed: {exc}"[:2000]
            experiment.status = ExperimentStatus.FAILED
            experiment.completed_at = datetime.now(UTC)
            _add_event(db, experiment, "failed", error=experiment.error_message)
            _add_audit(db, experiment, "experiment.failed", error=experiment.error_message)
    else:
        experiment.completed_at = datetime.now(UTC)
        if cancelled:
            experiment.status = ExperimentStatus.CANCELLED
            _add_event(db, experiment, "cancelled")
            _add_audit(db, experiment, "experiment.cancelled")
        else:
            experiment.status = ExperimentStatus.FAILED
            _add_event(db, experiment, "failed", error=experiment.error_message)
            _add_audit(db, experiment, "experiment.failed", error=experiment.error_message)

    await db.commit()
    return experiment
