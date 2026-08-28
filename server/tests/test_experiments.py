from __future__ import annotations

import asyncio
import json
import signal
import subprocess
import sys
import uuid
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import BackgroundTasks, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from server.api.experiments import (
    ExperimentProgressRequest,
    TargetProgressRequest,
    _enqueue_cleanup_job,
    _trim_event_type,
    _validate_progress_callback_token,
    report_experiment_progress,
)
from server.compute import ComputeRunner
from server.experiment_provisioner import (
    MockExperimentSSM,
    _parse_result,
    aggregate_instance_progress,
    ensure_instance_records,
    run_experiment,
)
from server.experiment_script import build_experiment_script
from server.experiment_terraform import (
    ExperimentInfraResult,
    ExperimentInfraRunner,
    RealExperimentTerraform,
)
from server.experiment_weights import ExperimentWeightError, allocate_weighted_pulls, normalize_weights
from server.models.experiment import Experiment, ExperimentEvent, ExperimentStatus
from server.models.user import User
from server.services.experiment_service import (
    ExperimentError,
    ImagePreflight,
    create_experiment,
    create_progress_token,
    validate_progress_token,
    validate_target_ref,
)
from server.ssm import CommandResult, SSMRunner
from server.worker.experiment_tasks import ExperimentWorkerSettings, task_cleanup_experiment
from server.worker.tasks import WorkerSettings

TARGET = (
    "ghcr.io/example/counter-test@"
    "sha256:a8bc7ee6699f9fd47d5c06d2d1b78da13c673582925d09e3356e11fc95740b7a"
)
TARGET_TWO = "ghcr.io/example/second-test@sha256:" + "b" * 64


class TrackingInfra(ExperimentInfraRunner):
    def __init__(self, fail_destroy: bool = False) -> None:
        self.applied = False
        self.destroyed = False
        self.fail_destroy = fail_destroy

    async def apply(
        self,
        experiment_id: str,
        instance_type: str,
        expires_at: str,
        safety_shutdown_minutes: int,
        instance_count: int = 1,
    ) -> ExperimentInfraResult:
        self.applied = True
        instance_ids = [
            "i-experiment-test" if index == 0 else f"i-experiment-test-{index + 1}"
            for index in range(instance_count)
        ]
        return ExperimentInfraResult(instance_id=instance_ids[0], instance_ids=instance_ids)

    async def destroy(self, experiment_id: str) -> None:
        self.destroyed = True
        if self.fail_destroy:
            raise RuntimeError("cleanup failed")


class FailingRemote(SSMRunner):
    async def run_command(self, instance_id: str, script: str, timeout_seconds: int = 600) -> CommandResult:
        return CommandResult(
            status="timedout",
            output="crane failed before producing a summary",
            stderr="execution timed out",
        )


class WrongWeightedDistributionRemote(SSMRunner):
    async def run_command(self, instance_id: str, script: str, timeout_seconds: int = 600) -> CommandResult:
        summary = {
            "requested": 3,
            "launched": 3,
            "successful": 3,
            "failed": 0,
            "max_concurrency": 1,
            "stop_reason": None,
            "targets": [
                {"target_ref": TARGET, "launched": 1, "successful": 1, "failed": 0},
                {"target_ref": TARGET_TWO, "launched": 2, "successful": 2, "failed": 0},
            ],
        }
        return CommandResult(status="success", output="FLARE_EXPERIMENT_RESULT=" + json.dumps(summary))


class InstantCompute(ComputeRunner):
    async def get_state(self, instance_id: str) -> str:
        return "running"

    async def start(self, instance_id: str, timeout_seconds: int = 300) -> None:
        return None

    async def stop(self, instance_id: str) -> None:
        return None


def make_counter(values: list[int]) -> Callable[[str], Awaitable[int]]:
    iterator = iter(values)

    async def read(_: str) -> int:
        return next(iterator)

    return read


async def no_sleep(_: float) -> None:
    return None


def make_experiment(user: User, **overrides) -> Experiment:
    defaults = {
        "id": uuid.uuid4(),
        "status": ExperimentStatus.PENDING,
        "created_by": user.id,
        "target_ref": TARGET,
        "package_url": "https://github.com/orgs/example/packages/container/package/counter-test",
        "targets": [
            {
                "requested_ref": TARGET,
                "target_ref": TARGET,
                "package_url": "https://github.com/orgs/example/packages/container/package/counter-test",
                "platform": "linux/amd64",
                "image_size_bytes": 1024,
                "layer_count": 1,
                "expected_pulls": 2,
                "launched_pulls": 0,
                "successful_pulls": 0,
                "failed_pulls": 0,
                "baseline_count": None,
                "current_count": None,
                "final_count": None,
            }
        ],
        "rate_per_minute": 2,
        "duration_minutes": 1,
        "expected_pulls": 2,
        "concurrency_limit": 4,
        "platform": "linux/amd64",
        "image_size_bytes": 1024,
        "layer_count": 1,
        "estimated_transfer_bytes": 2048,
        "instance_type": "t3.small",
        "terraform_state_key": "experiments/test/terraform.tfstate",
    }
    defaults.update(overrides)
    return Experiment(**defaults)


def test_parse_result_reports_missing_summary_count() -> None:
    with pytest.raises(RuntimeError, match="expected 1 result summary, found 0"):
        _parse_result("command output without a summary")


def test_parse_result_reports_duplicate_summary_count() -> None:
    summary = json.dumps(
        {"requested": 1, "launched": 1, "successful": 1, "failed": 0, "max_concurrency": 1}
    )
    output = f"FLARE_EXPERIMENT_RESULT={summary}\nFLARE_EXPERIMENT_RESULT={summary}"

    with pytest.raises(RuntimeError, match="expected 1 result summary, found 2"):
        _parse_result(output)


def test_progress_token_is_scoped_to_one_experiment() -> None:
    experiment_id = uuid.uuid4()
    token = create_progress_token(experiment_id)
    validate_progress_token(token, experiment_id)
    with pytest.raises(ExperimentError, match="Invalid progress token"):
        validate_progress_token(token, uuid.uuid4())

    member_token = create_progress_token(experiment_id, 2)
    validate_progress_token(member_token, experiment_id, 2)
    with pytest.raises(ExperimentError, match="Invalid progress token"):
        validate_progress_token(member_token, experiment_id, 1)

    # A new API accepts callbacks from a pre-fleet script during a rolling deploy.
    _validate_progress_callback_token(token, experiment_id, 0)
    with pytest.raises(ExperimentError, match="Invalid progress token"):
        _validate_progress_callback_token(token, experiment_id, 1)


def test_weighted_allocation_preserves_exact_total() -> None:
    assert allocate_weighted_pulls(1000, [2, 1]) == [667, 333]
    assert allocate_weighted_pulls(1000, [1, 1]) == [500, 500]
    assert allocate_weighted_pulls(1, [1, 1]) == [1, 0]
    assert normalize_weights(2, []) == [1, 1]
    with pytest.raises(ExperimentWeightError, match="positive integers"):
        normalize_weights(2, [1, 0])
    with pytest.raises(ExperimentWeightError, match="exactly one"):
        normalize_weights(2, [1])


async def test_cleanup_enqueue_none_is_a_failure_and_retries_have_fresh_job_ids() -> None:
    pool = SimpleNamespace(enqueue_job=AsyncMock(return_value=None))
    with (
        patch("server.api.experiments._experiment_pool", pool),
        pytest.raises(RuntimeError, match="not enqueued"),
    ):
        await _enqueue_cleanup_job(uuid.uuid4(), "cancel")
    assert "_job_id" not in pool.enqueue_job.await_args.kwargs


def test_target_requires_ghcr_tag_or_digest() -> None:
    assert validate_target_ref(TARGET).group("namespace") == "example"
    assert validate_target_ref("ghcr.io/example/counter-test:latest").group("tag") == "latest"
    for invalid in (
        "ghcr.io/example/counter-test",
        "docker.io/example/counter-test@sha256:" + "a" * 64,
        "ghcr.io/example/counter-test@sha256:short",
    ):
        try:
            validate_target_ref(invalid)
        except ExperimentError:
            pass
        else:
            raise AssertionError(f"invalid target accepted: {invalid}")


async def test_create_experiment_snapshots_single_configured_target(
    db: AsyncSession,
    admin_user: User,
) -> None:
    settings = SimpleNamespace(
        ghcr_experiments_enabled=True,
        ghcr_experiment_image=TARGET,
        ghcr_experiment_instance_type="t3.small",
        ghcr_experiment_max_rate_per_minute=48,
        ghcr_experiment_max_duration_minutes=60,
        ghcr_experiment_max_concurrency=4,
        ghcr_experiment_max_images=4,
        ghcr_experiment_max_transfer_gb=50,
    )
    with (
        patch("server.services.experiment_service.get_settings", return_value=settings),
        patch(
            "server.services.experiment_service.preflight_image",
            new=AsyncMock(
                return_value=ImagePreflight(
                    requested_ref=TARGET,
                    target_ref=TARGET,
                    package_url="https://github.com/orgs/example/packages/container/package/counter-test",
                    platform="linux/amd64",
                    image_size_bytes=1024,
                    layer_count=1,
                )
            ),
        ),
    ):
        experiment = await create_experiment(
            db,
            user=admin_user,
            target_refs=[TARGET],
            expected_resolved_refs=[TARGET],
            rate_per_minute=48,
            duration_minutes=5,
            concurrency_limit=4,
        )
        assert experiment.target_ref == TARGET
        assert experiment.expected_pulls == 240
        assert experiment.terraform_state_key == f"experiments/{experiment.id}/terraform.tfstate"

        with pytest.raises(ExperimentError, match="already active"):
            await create_experiment(
                db,
                user=admin_user,
                target_refs=[TARGET],
                expected_resolved_refs=[TARGET],
                rate_per_minute=1,
                duration_minutes=1,
                concurrency_limit=1,
            )


async def test_create_experiment_distributes_pulls_across_targets(
    db: AsyncSession,
    admin_user: User,
) -> None:
    settings = SimpleNamespace(
        ghcr_experiments_enabled=True,
        ghcr_experiment_instance_type="t3.small",
        ghcr_experiment_max_rate_per_minute=48,
        ghcr_experiment_max_duration_minutes=60,
        ghcr_experiment_max_concurrency=4,
        ghcr_experiment_max_images=4,
        ghcr_experiment_max_transfer_gb=50,
    )
    preflights = [
        ImagePreflight(TARGET, TARGET, "https://github.com/a", "linux/amd64", 1024, 1),
        ImagePreflight(TARGET_TWO, TARGET_TWO, "https://github.com/b", "linux/amd64", 2048, 2),
    ]
    with (
        patch("server.services.experiment_service.get_settings", return_value=settings),
        patch("server.services.experiment_service.preflight_image", new=AsyncMock(side_effect=preflights)),
    ):
        experiment = await create_experiment(
            db,
            user=admin_user,
            target_refs=[TARGET, TARGET_TWO],
            expected_resolved_refs=[TARGET, TARGET_TWO],
            rate_per_minute=4,
            duration_minutes=1,
            concurrency_limit=4,
            instance_count=2,
            target_weights=[2, 1],
        )

    assert experiment.instance_count == 2
    assert experiment.concurrency_limit == 4
    assert experiment.expected_pulls == 8
    assert [target["weight"] for target in experiment.targets] == [2, 1]
    assert [target["expected_pulls"] for target in experiment.targets] == [6, 2]
    assert [target["estimated_transfer_bytes"] for target in experiment.targets] == [6144, 4096]
    assert experiment.estimated_transfer_bytes == 10240
    assert len(experiment.instances) == 2


def test_script_uses_isolated_outputs_and_no_docker_daemon() -> None:
    script = build_experiment_script(
        [TARGET, TARGET_TWO],
        rate_per_minute=48,
        expected_pulls=240,
        concurrency_limit=3,
        progress_url="https://flare.example/api/experiments/id/progress",
        progress_token="signed-token",
        target_weights=[2, 1],
    )
    assert "REQUESTED=240" in script
    assert "target_quotas = [160, 80]" in script
    assert "next_target_index" in script
    assert "INTERVAL_SECONDS=1.250000000" in script
    assert 'env["DOCKER_CONFIG"] = str(config)' in script
    assert 'directory / "image.tar"' in script
    assert "docker pull" not in script
    assert "MAX_CONCURRENCY=3" in script
    assert "CANCELLED" in script
    assert "target_assigned" in script
    assert TARGET_TWO in script
    assert "https://flare.example/api/experiments/id/progress" in script
    assert "signed-token" in script


def test_generated_script_records_completed_pull_target(tmp_path: Path) -> None:
    script = build_experiment_script(
        [TARGET, TARGET_TWO],
        rate_per_minute=120,
        expected_pulls=6,
        concurrency_limit=2,
        progress_url="http://127.0.0.1:1/progress",
        progress_token="signed-token",
        target_weights=[2, 1],
    )
    python_source = script.split("<<'PY'\n", 1)[1].rsplit("\nPY\n", 1)[0]

    fake_crane = tmp_path / "crane"
    fake_crane.write_text(
        """#!/usr/bin/env python3
import io
import sys
import tarfile

payload = b"image"
with tarfile.open(sys.argv[3], "w") as archive:
    member = tarfile.TarInfo("manifest.json")
    member.size = len(payload)
    archive.addfile(member, io.BytesIO(payload))
"""
    )
    fake_crane.chmod(0o755)
    trials = tmp_path / "trials"
    trials.mkdir()

    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            python_source,
            str(fake_crane),
            json.dumps([TARGET, TARGET_TWO]),
            str(trials),
            "6",
            "0.5",
            "2",
            "http://127.0.0.1:1/progress",
            "signed-token",
            str(tmp_path / "CANCELLED"),
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    result_lines = [
        line for line in completed.stdout.splitlines() if line.startswith("FLARE_EXPERIMENT_RESULT=")
    ]
    assert len(result_lines) == 1
    summary = json.loads(result_lines[0].split("=", 1)[1])
    assert summary["launched"] == 6
    assert summary["successful"] == 6
    assert summary["failed"] == 0
    assert summary["targets"] == [
        {"target_ref": TARGET, "launched": 4, "successful": 4, "failed": 0},
        {"target_ref": TARGET_TWO, "launched": 2, "successful": 2, "failed": 0},
    ]


def test_generated_script_allows_same_image_overlap_above_image_count(tmp_path: Path) -> None:
    script = build_experiment_script(
        [TARGET, TARGET_TWO],
        rate_per_minute=1200,
        expected_pulls=8,
        concurrency_limit=4,
        progress_url="http://127.0.0.1:1/progress",
        progress_token="signed-token",
        target_weights=[1, 1],
    )
    python_source = script.split("<<'PY'\n", 1)[1].rsplit("\nPY\n", 1)[0]

    fake_crane = tmp_path / "slow-crane"
    fake_crane.write_text(
        """#!/usr/bin/env python3
import io
import sys
import tarfile
import time

time.sleep(0.35)
payload = b"image"
with tarfile.open(sys.argv[3], "w") as archive:
    member = tarfile.TarInfo("manifest.json")
    member.size = len(payload)
    archive.addfile(member, io.BytesIO(payload))
"""
    )
    fake_crane.chmod(0o755)
    trials = tmp_path / "overlap-trials"
    trials.mkdir()

    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            python_source,
            str(fake_crane),
            json.dumps([TARGET, TARGET_TWO]),
            str(trials),
            "8",
            "0.05",
            "4",
            "http://127.0.0.1:1/progress",
            "signed-token",
            str(tmp_path / "CANCELLED"),
        ],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    result_line = next(
        line for line in completed.stdout.splitlines() if line.startswith("FLARE_EXPERIMENT_RESULT=")
    )
    summary = json.loads(result_line.split("=", 1)[1])
    assert 2 < summary["max_concurrency"] <= 4
    assert summary["launched"] == 8
    assert summary["successful"] == 8
    assert summary["targets"] == [
        {"target_ref": TARGET, "launched": 4, "successful": 4, "failed": 0},
        {"target_ref": TARGET_TWO, "launched": 4, "successful": 4, "failed": 0},
    ]


async def test_terraform_process_is_terminated_and_reaped_when_job_is_cancelled() -> None:
    finished = asyncio.Event()

    class FakeProcess:
        pid = 12345
        returncode = 0

        async def communicate(self):
            await finished.wait()
            return b"", b""

    process = FakeProcess()
    signals: list[int] = []

    def kill_process_group(pid: int, sent_signal: int) -> None:
        assert pid == process.pid
        signals.append(sent_signal)
        finished.set()

    runner = object.__new__(RealExperimentTerraform)
    with (
        patch("server.experiment_terraform.asyncio.create_subprocess_exec", new=AsyncMock(return_value=process)),
        patch("server.experiment_terraform.os.killpg", side_effect=kill_process_group),
    ):
        task = asyncio.create_task(runner._run(["apply"], "/tmp", timeout_seconds=60))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert signals == [signal.SIGTERM]


def test_worker_queues_are_separate() -> None:
    assert ExperimentWorkerSettings.queue_name == "arq:experiments"
    assert not hasattr(WorkerSettings, "queue_name")
    assert ExperimentWorkerSettings.max_jobs == 2
    assert WorkerSettings.max_jobs == 4


def test_fleet_concurrency_uses_current_active_total_not_historical_peaks() -> None:
    experiment = SimpleNamespace(
        instances=[],
        targets=[{"target_ref": TARGET}],
        launched_pulls=0,
        successful_pulls=0,
        failed_pulls=0,
        active_pulls=0,
        max_concurrency=None,
    )
    instances = [
        {
            "launched_pulls": 10,
            "successful_pulls": 8,
            "failed_pulls": 0,
            "active_pulls": 2,
            "max_concurrency": 4,
            "targets": [{"target_ref": TARGET, "launched": 10, "successful": 8, "failed": 0}],
        },
        {
            "launched_pulls": 10,
            "successful_pulls": 9,
            "failed_pulls": 0,
            "active_pulls": 1,
            "max_concurrency": 4,
            "targets": [{"target_ref": TARGET, "launched": 10, "successful": 9, "failed": 0}],
        },
    ]

    aggregate_instance_progress(experiment, instances)

    assert experiment.active_pulls == 3
    assert experiment.max_concurrency == 3


def test_legacy_instance_targets_are_normalized() -> None:
    experiment = SimpleNamespace(
        instance_count=1,
        targets=[{"target_ref": TARGET}],
        instances=[
            {
                "index": 0,
                "targets": [
                    {
                        "target_ref": TARGET,
                        "launched_pulls": 4,
                        "successful_pulls": 3,
                        "failed_pulls": 1,
                    }
                ],
            }
        ],
    )

    targets = ensure_instance_records(experiment)[0]["targets"]
    assert targets == [
        {"target_ref": TARGET, "launched": 4, "successful": 3, "failed": 1, "active": 0}
    ]


async def test_experiment_pipeline_completes_and_always_destroys(db: AsyncSession, admin_user: User) -> None:
    experiment = make_experiment(admin_user)
    db.add(experiment)
    await db.commit()
    infra = TrackingInfra()

    result = await run_experiment(
        db,
        experiment,
        infra=infra,
        remote=MockExperimentSSM(),
        compute=InstantCompute(),
        count_reader=make_counter([10, 12, 12]),
        sleep=no_sleep,
    )

    assert infra.applied is True
    assert infra.destroyed is True
    assert result.status == ExperimentStatus.COMPLETED
    assert result.instance_id is None
    assert result.successful_pulls == 2
    assert result.baseline_count == 10
    assert result.immediate_count == 12
    assert result.delayed_count == 12
    assert result.destroyed_at is not None
    assert result.instance_count == 1
    assert result.instances[0]["instance_id"] == "i-experiment-test"
    assert result.instances[0]["cleanup_status"] == "destroyed"


async def test_experiment_pipeline_runs_a_fleet_and_aggregates_results(
    db: AsyncSession,
    admin_user: User,
) -> None:
    target = make_experiment(admin_user).targets[0]
    target["expected_pulls"] = 6
    experiment = make_experiment(
        admin_user,
        instance_count=3,
        expected_pulls=6,
        estimated_transfer_bytes=6144,
        targets=[target],
    )
    db.add(experiment)
    await db.commit()
    infra = TrackingInfra()

    result = await run_experiment(
        db,
        experiment,
        infra=infra,
        remote=MockExperimentSSM(),
        compute=InstantCompute(),
        count_reader=make_counter([10, 16, 16]),
        sleep=no_sleep,
    )

    assert result.status == ExperimentStatus.COMPLETED
    assert result.successful_pulls == 6
    assert result.expected_pulls == 6
    assert len(result.instances) == 3
    assert [item["successful_pulls"] for item in result.instances] == [2, 2, 2]
    assert all(item["status"] == "completed" for item in result.instances)
    assert all(item["cleanup_status"] == "destroyed" for item in result.instances)
    assert result.targets[0]["successful_pulls"] == 6
    assert result.instance_id is None


async def test_progress_callback_cannot_exceed_weighted_target_quota(
    db: AsyncSession,
    admin_user: User,
) -> None:
    first = dict(make_experiment(admin_user).targets[0])
    first.update(weight=2, expected_pulls=2)
    second = dict(first)
    second.update(
        requested_ref=TARGET_TWO,
        target_ref=TARGET_TWO,
        package_url="https://github.com/orgs/example/packages/container/package/second-test",
        weight=1,
        expected_pulls=1,
    )
    experiment = make_experiment(
        admin_user,
        status=ExperimentStatus.RUNNING,
        rate_per_minute=3,
        expected_pulls=3,
        targets=[first, second],
    )
    db.add(experiment)
    await db.commit()
    body = ExperimentProgressRequest(
        instance_index=0,
        launched=3,
        successful=3,
        failed=0,
        active=0,
        max_concurrency=1,
        elapsed_seconds=60,
        targets=[
            TargetProgressRequest(target_ref=TARGET, launched=1, successful=1, failed=0, active=0),
            TargetProgressRequest(target_ref=TARGET_TWO, launched=2, successful=2, failed=0, active=0),
        ],
    )

    with pytest.raises(HTTPException, match="weighted quota") as error:
        await report_experiment_progress(
            experiment.id,
            body,
            db,
            BackgroundTasks(),
            authorization=f"Bearer {create_progress_token(experiment.id, 0)}",
        )

    assert error.value.status_code == 400

    aggregate_short = ExperimentProgressRequest(
        instance_index=0,
        launched=3,
        successful=2,
        failed=0,
        active=0,
        max_concurrency=1,
        elapsed_seconds=60,
        targets=[
            TargetProgressRequest(target_ref=TARGET, launched=2, successful=1, failed=0, active=0),
            TargetProgressRequest(target_ref=TARGET_TWO, launched=1, successful=1, failed=0, active=0),
        ],
    )
    with pytest.raises(HTTPException, match="Invalid progress totals"):
        await report_experiment_progress(
            experiment.id,
            aggregate_short,
            db,
            BackgroundTasks(),
            authorization=f"Bearer {create_progress_token(experiment.id, 0)}",
        )

    per_target_mismatch = ExperimentProgressRequest(
        instance_index=0,
        launched=3,
        successful=2,
        failed=1,
        active=0,
        max_concurrency=1,
        elapsed_seconds=60,
        targets=[
            TargetProgressRequest(target_ref=TARGET, launched=2, successful=1, failed=0, active=0),
            TargetProgressRequest(target_ref=TARGET_TWO, launched=1, successful=1, failed=1, active=0),
        ],
    )
    with pytest.raises(HTTPException, match="Invalid per-target progress totals"):
        await report_experiment_progress(
            experiment.id,
            per_target_mismatch,
            db,
            BackgroundTasks(),
            authorization=f"Bearer {create_progress_token(experiment.id, 0)}",
        )


async def test_final_summary_must_match_each_weighted_quota(
    db: AsyncSession,
    admin_user: User,
) -> None:
    first = dict(make_experiment(admin_user).targets[0])
    first.update(weight=2, expected_pulls=2)
    second = dict(first)
    second.update(
        requested_ref=TARGET_TWO,
        target_ref=TARGET_TWO,
        package_url="https://github.com/orgs/example/packages/container/package/second-test",
        weight=1,
        expected_pulls=1,
    )
    experiment = make_experiment(
        admin_user,
        rate_per_minute=3,
        expected_pulls=3,
        targets=[first, second],
        image_size_bytes=2048,
        layer_count=2,
        estimated_transfer_bytes=3072,
    )
    db.add(experiment)
    await db.commit()

    result = await run_experiment(
        db,
        experiment,
        infra=TrackingInfra(),
        remote=WrongWeightedDistributionRemote(),
        compute=InstantCompute(),
        count_reader=make_counter([10, 20]),
        sleep=no_sleep,
    )

    assert result.successful_pulls == 0
    assert result.status == ExperimentStatus.FAILED
    assert "weighted quota" in (result.error_message or "")


async def test_cancelled_experiment_does_not_launch_and_still_cleans_up(
    db: AsyncSession,
    admin_user: User,
) -> None:
    experiment = make_experiment(admin_user, cancellation_requested=True)
    db.add(experiment)
    await db.commit()
    infra = TrackingInfra()

    result = await run_experiment(
        db,
        experiment,
        infra=infra,
        remote=FailingRemote(),
        compute=InstantCompute(),
        count_reader=make_counter([]),
        sleep=no_sleep,
    )

    assert infra.applied is False
    assert infra.destroyed is True
    assert result.status == ExperimentStatus.CANCELLED
    assert result.launched_pulls == 0


async def test_command_failure_still_destroys_infrastructure(db: AsyncSession, admin_user: User) -> None:
    experiment = make_experiment(admin_user)
    db.add(experiment)
    await db.commit()
    infra = TrackingInfra()

    result = await run_experiment(
        db,
        experiment,
        infra=infra,
        remote=FailingRemote(),
        compute=InstantCompute(),
        count_reader=make_counter([10]),
        sleep=no_sleep,
    )

    assert infra.destroyed is True
    assert result.status == ExperimentStatus.FAILED
    assert result.instance_id is None
    assert result.destroyed_at is not None
    assert "SSM command timedout; expected 1 result summary, found 0" in (
        result.error_message or ""
    )
    assert "stderr: execution timed out" in (result.error_message or "")


async def test_progress_event_retention_is_hard_capped(
    db: AsyncSession,
    admin_user: User,
) -> None:
    experiment = make_experiment(admin_user)
    db.add(experiment)
    await db.commit()
    db.add_all(
        [
            ExperimentEvent(
                experiment_id=experiment.id,
                event_type="progress",
                payload={"sequence": sequence},
            )
            for sequence in range(8)
        ]
    )
    await db.flush()

    await _trim_event_type(db, experiment.id, "progress", 3)
    await db.commit()

    count = await db.scalar(
        select(func.count()).select_from(ExperimentEvent).where(
            ExperimentEvent.experiment_id == experiment.id,
            ExperimentEvent.event_type == "progress",
        )
    )
    assert count == 3


async def test_safety_cleanup_does_not_overwrite_completed_experiment(
    db: AsyncSession,
    admin_user: User,
) -> None:
    experiment = make_experiment(
        admin_user,
        status=ExperimentStatus.COMPLETED,
        completed_at=datetime.now(UTC),
        destroyed_at=datetime.now(UTC),
    )
    db.add(experiment)
    await db.commit()
    infra = TrackingInfra()

    @asynccontextmanager
    async def test_session():
        yield db

    with (
        patch("server.worker.experiment_tasks._infra", return_value=infra),
        patch("server.worker.experiment_tasks.async_session", test_session),
    ):
        await task_cleanup_experiment({}, str(experiment.id), "safety")

    await db.refresh(experiment)
    assert experiment.status == ExperimentStatus.COMPLETED
    assert infra.destroyed is False


async def test_cleanup_failure_is_not_reported_as_destroyed(db: AsyncSession, admin_user: User) -> None:
    experiment = make_experiment(admin_user)
    db.add(experiment)
    await db.commit()
    infra = TrackingInfra(fail_destroy=True)

    result = await run_experiment(
        db,
        experiment,
        infra=infra,
        remote=MockExperimentSSM(),
        compute=InstantCompute(),
        count_reader=make_counter([10, 12]),
        sleep=no_sleep,
    )

    assert result.status == ExperimentStatus.CLEANUP_FAILED
    assert result.instance_id == "i-experiment-test"
    assert result.destroyed_at is None
    assert result.cleanup_error == "cleanup failed"
