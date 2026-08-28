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
from server.experiment_weights import allocate_weighted_pulls, normalize_weights
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
        quotas_line = next(line for line in script.splitlines() if line.startswith("TARGET_QUOTAS_JSON="))
        target_quotas = json.loads(shlex.split(quotas_line.split("=", 1)[1])[0])
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
                    "launched": target_quotas[index],
                    "successful": target_quotas[index],
                    "failed": 0,
                    "active": 0,
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


def _validate_member_summary(
    summary: dict,
    target_refs: list[str],
    quotas: list[int],
) -> bool:
    """Validate aggregate consistency and each target's weighted final quota."""
    numeric_fields = ("requested", "launched", "successful", "failed", "max_concurrency")
    if any(
        isinstance(summary.get(field), bool)
        or not isinstance(summary.get(field), int)
        or summary[field] < 0
        for field in numeric_fields
    ):
        raise RuntimeError("Experiment result summary contains invalid numeric totals")
    if summary["requested"] != sum(quotas) or summary["launched"] > summary["requested"]:
        raise RuntimeError("Experiment result summary does not match the requested pull count")
    target_results = summary.get("targets")
    if not isinstance(target_results, list) or len(target_results) != len(target_refs):
        raise RuntimeError("Experiment result summary has invalid target results")
    by_ref: dict[str, dict] = {}
    for target in target_results:
        if not isinstance(target, dict) or target.get("target_ref") not in target_refs:
            raise RuntimeError("Experiment result summary contains an unknown target")
        target_ref = target["target_ref"]
        if target_ref in by_ref:
            raise RuntimeError("Experiment result summary contains duplicate targets")
        for field in ("launched", "successful", "failed"):
            value = target.get(field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise RuntimeError("Experiment target result contains invalid totals")
        if target["successful"] + target["failed"] != target["launched"]:
            raise RuntimeError("Experiment target result totals are inconsistent")
        by_ref[target_ref] = target
    if sum(item["launched"] for item in target_results) != summary["launched"]:
        raise RuntimeError("Experiment target launches do not match the aggregate")
    if sum(item["successful"] for item in target_results) != summary["successful"]:
        raise RuntimeError("Experiment target successes do not match the aggregate")
    if sum(item["failed"] for item in target_results) != summary["failed"]:
        raise RuntimeError("Experiment target failures do not match the aggregate")
    if summary["successful"] + summary["failed"] != summary["launched"]:
        raise RuntimeError("Experiment result summary totals are inconsistent")

    for target_ref, quota in zip(target_refs, quotas, strict=True):
        if by_ref[target_ref]["launched"] > quota:
            raise RuntimeError("Experiment target result exceeds its weighted quota")
    return all(
        by_ref[target_ref]["launched"] == quota
        and by_ref[target_ref]["successful"] == quota
        and by_ref[target_ref]["failed"] == 0
        for target_ref, quota in zip(target_refs, quotas, strict=True)
    )


def _new_instance(index: int, target_refs: list[str]) -> dict:
    return {
        "index": index,
        "instance_id": None,
        "status": "pending",
        "cleanup_status": "not_started",
        "launched_pulls": 0,
        "successful_pulls": 0,
        "failed_pulls": 0,
        "active_pulls": 0,
        "max_concurrency": 0,
        "last_progress_at": None,
        "last_progress_event_at": None,
        "error_message": None,
        "run_log": None,
        "targets": [
            {"target_ref": ref, "launched": 0, "successful": 0, "failed": 0, "active": 0}
            for ref in target_refs
        ],
    }


def ensure_instance_records(experiment: Experiment) -> list[dict]:
    """Return a complete mutable fleet snapshot, including legacy runs."""
    count = experiment.instance_count or 1
    refs = [item["target_ref"] for item in experiment.targets]
    existing = {int(item.get("index", index)): dict(item) for index, item in enumerate(experiment.instances or [])}
    records: list[dict] = []
    for index in range(count):
        record = _new_instance(index, refs)
        record.update(existing.get(index, {}))
        if not record.get("targets"):
            record["targets"] = _new_instance(index, refs)["targets"]
        else:
            # Migration/rolling-deploy compatibility: experiment-level targets
            # use *_pulls keys while instance targets use concise keys.
            record["targets"] = [
                {
                    "target_ref": target["target_ref"],
                    "launched": int(target.get("launched", target.get("launched_pulls", 0))),
                    "successful": int(target.get("successful", target.get("successful_pulls", 0))),
                    "failed": int(target.get("failed", target.get("failed_pulls", 0))),
                    "active": int(target.get("active", 0)),
                }
                for target in record["targets"]
            ]
        records.append(record)
    return records


def aggregate_instance_progress(experiment: Experiment, instances: list[dict]) -> None:
    """Persist a fleet snapshot and derive all authoritative fleet totals."""
    experiment.instances = instances
    experiment.launched_pulls = sum(int(item.get("launched_pulls", 0)) for item in instances)
    experiment.successful_pulls = sum(int(item.get("successful_pulls", 0)) for item in instances)
    experiment.failed_pulls = sum(int(item.get("failed_pulls", 0)) for item in instances)
    experiment.active_pulls = sum(int(item.get("active_pulls", 0)) for item in instances)
    # Fleet concurrency is sampled from the members' latest simultaneous active
    # counts. Summing historical per-member peaks can combine different moments
    # and overstate concurrency that never actually occurred.
    if len(instances) == 1:
        observed_concurrency = int(instances[0].get("max_concurrency", 0))
    else:
        observed_concurrency = experiment.active_pulls
    if observed_concurrency > 0:
        experiment.max_concurrency = max(experiment.max_concurrency or 0, observed_concurrency)

    target_totals: dict[str, dict[str, int]] = {}
    for instance in instances:
        for target in instance.get("targets", []):
            totals = target_totals.setdefault(
                target["target_ref"], {"launched": 0, "successful": 0, "failed": 0}
            )
            totals["launched"] += int(target.get("launched", 0))
            totals["successful"] += int(target.get("successful", 0))
            totals["failed"] += int(target.get("failed", 0))
    targets = [dict(item) for item in experiment.targets]
    for target in targets:
        totals = target_totals.get(target["target_ref"], {})
        target["launched_pulls"] = totals.get("launched", 0)
        target["successful_pulls"] = totals.get("successful", 0)
        target["failed_pulls"] = totals.get("failed", 0)
    experiment.targets = targets


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
    db.add(AuditLog(user_id=experiment.created_by, site_id=None, action=action, details=details))


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

        instances = ensure_instance_records(experiment)
        for instance in instances:
            instance["status"] = "provisioning"
        experiment.instances = instances
        experiment.status = ExperimentStatus.PROVISIONING
        _add_event(db, experiment, "provisioning", instance_count=experiment.instance_count)
        experiment.baseline_count = await _update_target_counts(experiment, count_reader, "baseline_count")
        _add_event(db, experiment, "baseline_recorded", count=experiment.baseline_count)
        await db.commit()

        # A full-day run gets an extra hour for bootstrapping and orderly
        # teardown. The janitor provides an independent final safety net.
        safety_minutes = experiment.duration_minutes + 60
        expires_at = datetime.now(UTC) + timedelta(minutes=safety_minutes)
        if experiment.instance_count == 1:
            # Preserve compatibility with existing single-instance runners.
            result = await infra.apply(
                str(experiment.id), experiment.instance_type, expires_at.isoformat(), safety_minutes
            )
        else:
            result = await infra.apply(
                str(experiment.id),
                experiment.instance_type,
                expires_at.isoformat(),
                safety_minutes,
                experiment.instance_count,
            )
        instance_ids = result.all_instance_ids
        if len(instance_ids) != experiment.instance_count:
            raise RuntimeError(
                f"Infrastructure returned {len(instance_ids)} instances; expected {experiment.instance_count}"
            )

        instances = ensure_instance_records(experiment)
        for instance, instance_id in zip(instances, instance_ids, strict=True):
            instance["instance_id"] = instance_id
            instance["status"] = "starting"
        experiment.instances = instances
        experiment.instance_id = instance_ids[0]
        _add_event(db, experiment, "infrastructure_provisioned", instance_ids=instance_ids)
        await db.commit()
        await db.refresh(experiment)
        if experiment.cancellation_requested:
            raise ExperimentCancelledError("Experiment cancelled by administrator")

        start_results = await asyncio.gather(
            *(compute.start(instance_id, timeout_seconds=600) for instance_id in instance_ids),
            return_exceptions=True,
        )
        instances = ensure_instance_records(experiment)
        ready: list[tuple[int, str]] = []
        start_errors: list[str] = []
        for instance, instance_id, start_result in zip(instances, instance_ids, start_results, strict=True):
            if isinstance(start_result, BaseException):
                instance["status"] = "failed"
                instance["error_message"] = f"Instance startup failed: {start_result}"[:2000]
                start_errors.append(f"{instance_id}: {start_result}")
            else:
                instance["status"] = "running"
                ready.append((int(instance["index"]), instance_id))
        experiment.instances = instances
        await db.refresh(experiment, attribute_names=["cancellation_requested"])
        if experiment.cancellation_requested:
            raise ExperimentCancelledError("Experiment cancelled by administrator")
        if not ready:
            raise RuntimeError("No fleet instances became ready: " + "; ".join(start_errors))

        experiment.status = ExperimentStatus.RUNNING
        experiment.started_at = datetime.now(UTC)
        _add_event(
            db,
            experiment,
            "running",
            expected_pulls=experiment.expected_pulls,
            ready_instances=len(ready),
            instance_count=experiment.instance_count,
        )
        _add_audit(db, experiment, "experiment.started", instance_ids=instance_ids)
        await db.commit()

        settings = get_settings()
        progress_url = f"{settings.flare_base_url.rstrip('/')}/api/experiments/{experiment.id}/progress"
        expected_per_instance = experiment.rate_per_minute * experiment.duration_minutes
        command_timeout = experiment.duration_minutes * 60 + 30 * 60

        async def run_member(index: int, instance_id: str) -> CommandResult:
            script = build_experiment_script(
                [item["target_ref"] for item in experiment.targets],
                experiment.rate_per_minute,
                expected_per_instance,
                experiment.concurrency_limit,
                progress_url,
                create_progress_token(experiment.id, index),
                instance_index=index,
                target_weights=[int(item.get("weight", 1)) for item in experiment.targets],
            )
            return await remote.run_command(instance_id, script, timeout_seconds=command_timeout)

        command_results = await asyncio.gather(
            *(run_member(index, instance_id) for index, instance_id in ready),
            return_exceptions=True,
        )

        # Progress callbacks use separate sessions, so reload before applying
        # final command summaries to avoid overwriting a newer callback.
        await db.refresh(experiment)
        instances = ensure_instance_records(experiment)
        summaries: list[dict] = []
        member_errors = list(start_errors)
        logs: list[str] = []
        target_refs = [item["target_ref"] for item in experiment.targets]
        target_weights = normalize_weights(
            len(experiment.targets),
            [int(item.get("weight", 1)) for item in experiment.targets],
        )
        target_quotas = allocate_weighted_pulls(expected_per_instance, target_weights)
        for (index, instance_id), command_result in zip(ready, command_results, strict=True):
            instance = instances[index]
            if isinstance(command_result, BaseException):
                message = f"SSM command failed: {command_result}"
                instance["status"] = "failed"
                instance["error_message"] = message[:2000]
                member_errors.append(f"{instance_id}: {message}")
                continue
            instance["run_log"] = command_result.output[-2000:]
            logs.append(f"[{instance_id}]\n{command_result.output[-2000:]}")
            try:
                summary = _parse_result(command_result.output)
                weighted_complete = _validate_member_summary(summary, target_refs, target_quotas)
            except Exception as exc:
                instance["status"] = "failed"
                instance["error_message"] = str(exc)[:2000]
                member_errors.append(f"{instance_id}: {exc}")
                continue
            summaries.append({"instance_index": index, "instance_id": instance_id, **summary})
            instance["launched_pulls"] = int(summary["launched"])
            instance["successful_pulls"] = int(summary["successful"])
            instance["failed_pulls"] = int(summary["failed"])
            instance["active_pulls"] = 0
            instance["max_concurrency"] = int(summary["max_concurrency"])
            instance["last_progress_at"] = datetime.now(UTC).isoformat()
            instance["targets"] = summary.get("targets", instance["targets"])
            complete = (
                command_result.status == "success"
                and int(summary["successful"]) == expected_per_instance
                and int(summary["launched"]) == expected_per_instance
                and weighted_complete
            )
            instance["status"] = "completed" if complete else "failed"
            if not complete:
                reason = summary.get("stop_reason") or command_result.status
                instance["error_message"] = f"Instance completed incompletely: {reason}"[:2000]
                member_errors.append(f"{instance_id}: {instance['error_message']}")

        aggregate_instance_progress(experiment, instances)
        experiment.results = {"instances": summaries}
        experiment.run_log = "\n\n".join(logs)[-10000:] or None
        _add_event(db, experiment, "run_completed", instances=summaries)
        await db.commit()
        await db.refresh(experiment)
        if experiment.cancellation_requested:
            raise ExperimentCancelledError("Experiment cancelled by administrator")
        if member_errors:
            raise RuntimeError("Fleet member failures: " + "; ".join(member_errors))
        if experiment.successful_pulls != experiment.expected_pulls:
            raise RuntimeError(
                f"Experiment completed {experiment.successful_pulls}/{experiment.expected_pulls} pulls"
            )

        await sleep(10)
        experiment.immediate_count = await _update_target_counts(experiment, count_reader, "current_count")
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
        # Cancellation may have been requested while an SSM/Terraform call was
        # in flight and caused that call to fail with a generic transport error.
        cancelled = cancelled or experiment.cancellation_requested
        instances = ensure_instance_records(experiment)
        for instance in instances:
            if instance["status"] in {"pending", "provisioning", "starting", "running"}:
                instance["status"] = "cancelled" if cancelled else "failed"
                if not cancelled and not instance.get("error_message"):
                    instance["error_message"] = str(exc)[:2000]
                instance["active_pulls"] = 0
        aggregate_instance_progress(experiment, instances)
        experiment.error_message = str(exc)[:2000]
        await db.commit()
    finally:
        experiment.status = ExperimentStatus.DESTROYING
        experiment.active_pulls = 0
        instances = ensure_instance_records(experiment)
        for instance in instances:
            if instance.get("instance_id"):
                instance["cleanup_status"] = "destroying"
        experiment.instances = instances
        _add_event(db, experiment, "destroying", instance_ids=[i.get("instance_id") for i in instances])
        await db.commit()
        try:
            await infra.destroy(str(experiment.id))
        except Exception as cleanup_exc:
            logger.exception("GHCR experiment cleanup failed id=%s", experiment.id)
            instances = ensure_instance_records(experiment)
            for instance in instances:
                if instance.get("instance_id"):
                    instance["cleanup_status"] = "failed"
            experiment.instances = instances
            experiment.status = ExperimentStatus.CLEANUP_FAILED
            experiment.cleanup_error = str(cleanup_exc)[:2000]
            _add_event(db, experiment, "cleanup_failed", error=experiment.cleanup_error)
            _add_audit(db, experiment, "experiment.cleanup_failed", error=experiment.cleanup_error)
            await db.commit()
            return experiment

        instances = ensure_instance_records(experiment)
        for instance in instances:
            if instance.get("instance_id"):
                instance["cleanup_status"] = "destroyed"
        experiment.instances = instances
        experiment.instance_id = None
        experiment.destroyed_at = datetime.now(UTC)
        _add_event(db, experiment, "infrastructure_destroyed", instance_count=experiment.instance_count)
        await db.commit()

    if run_error is None:
        await sleep(90)
        try:
            experiment.delayed_count = await _update_target_counts(experiment, count_reader, "final_count")
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
                instance_count=experiment.instance_count,
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
