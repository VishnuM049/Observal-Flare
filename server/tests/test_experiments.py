from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from server.compute import ComputeRunner
from server.experiment_provisioner import MockExperimentSSM, run_experiment
from server.experiment_script import build_experiment_script
from server.experiment_terraform import ExperimentInfraResult, ExperimentInfraRunner
from server.models.experiment import Experiment, ExperimentStatus
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
from server.worker.experiment_tasks import ExperimentWorkerSettings
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
    ) -> ExperimentInfraResult:
        self.applied = True
        return ExperimentInfraResult(instance_id="i-experiment-test")

    async def destroy(self, experiment_id: str) -> None:
        self.destroyed = True
        if self.fail_destroy:
            raise RuntimeError("cleanup failed")


class FailingRemote(SSMRunner):
    async def run_command(self, instance_id: str, script: str, timeout_seconds: int = 600) -> CommandResult:
        return CommandResult(status="failed", output="crane failed before producing a summary")


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


def test_progress_token_is_scoped_to_one_experiment() -> None:
    experiment_id = uuid.uuid4()
    token = create_progress_token(experiment_id)
    validate_progress_token(token, experiment_id)
    with pytest.raises(ExperimentError, match="Invalid progress token"):
        validate_progress_token(token, uuid.uuid4())


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
            rate_per_minute=3,
            duration_minutes=1,
            concurrency_limit=2,
        )

    assert [target["expected_pulls"] for target in experiment.targets] == [2, 1]
    assert experiment.estimated_transfer_bytes == 4096


def test_script_uses_isolated_outputs_and_no_docker_daemon() -> None:
    script = build_experiment_script(
        [TARGET, TARGET_TWO],
        rate_per_minute=48,
        expected_pulls=240,
        concurrency_limit=3,
        progress_url="https://flare.example/api/experiments/id/progress",
        progress_token="signed-token",
    )
    assert "REQUESTED=240" in script
    assert "INTERVAL_SECONDS=1.250000000" in script
    assert 'env["DOCKER_CONFIG"] = str(config)' in script
    assert 'directory / "image.tar"' in script
    assert "docker pull" not in script
    assert "MAX_CONCURRENCY=3" in script
    assert "CANCELLED" in script
    assert "target still active at scheduled start" in script
    assert TARGET_TWO in script
    assert "https://flare.example/api/experiments/id/progress" in script
    assert "signed-token" in script


def test_worker_queues_are_separate() -> None:
    assert ExperimentWorkerSettings.queue_name == "arq:experiments"
    assert not hasattr(WorkerSettings, "queue_name")
    assert ExperimentWorkerSettings.max_jobs == 1
    assert WorkerSettings.max_jobs == 4


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
    assert "result summary" in (result.error_message or "")


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
