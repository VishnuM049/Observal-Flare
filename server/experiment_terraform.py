from __future__ import annotations

import abc
import asyncio
import contextlib
import json
import logging
import os
import shutil
import signal
import tempfile
from dataclasses import dataclass
from pathlib import Path

from server.config import get_settings

logger = logging.getLogger(__name__)
EXPERIMENT_TF_MODULE_DIR = Path("/app/infra/experiment")
TERRAFORM_INIT_TIMEOUT_SECONDS = 300
TERRAFORM_APPLY_TIMEOUT_SECONDS = 1800
TERRAFORM_DESTROY_TIMEOUT_SECONDS = 1800
TERRAFORM_OUTPUT_TIMEOUT_SECONDS = 120


@dataclass
class ExperimentInfraResult:
    # instance_id remains for compatibility with the original single-instance
    # experiment implementation and external test doubles.
    instance_id: str
    instance_ids: list[str] | None = None

    @property
    def all_instance_ids(self) -> list[str]:
        return self.instance_ids or [self.instance_id]


class ExperimentInfraRunner(abc.ABC):
    @abc.abstractmethod
    async def apply(
        self,
        experiment_id: str,
        instance_type: str,
        expires_at: str,
        safety_shutdown_minutes: int,
        instance_count: int = 1,
    ) -> ExperimentInfraResult:
        """Provision isolated experiment infrastructure."""

    @abc.abstractmethod
    async def destroy(self, experiment_id: str) -> None:
        """Destroy isolated experiment infrastructure."""


class RealExperimentTerraform(ExperimentInfraRunner):
    def __init__(self) -> None:
        settings = get_settings()
        self._bucket = settings.terraform_state_bucket
        self._lock_table = settings.terraform_lock_table
        self._region = settings.aws_region

    def _backend_config(self, experiment_id: str) -> list[str]:
        return [
            f"-backend-config=bucket={self._bucket}",
            f"-backend-config=key=experiments/{experiment_id}/terraform.tfstate",
            f"-backend-config=region={self._region}",
            f"-backend-config=dynamodb_table={self._lock_table}",
        ]

    def _make_workdir(self) -> str:
        workdir = tempfile.mkdtemp(prefix="flare-experiment-tf-")
        shutil.copytree(EXPERIMENT_TF_MODULE_DIR, workdir, dirs_exist_ok=True)
        return workdir

    async def _run(self, args: list[str], cwd: str, timeout_seconds: int) -> str:
        proc = await asyncio.create_subprocess_exec(
            "terraform",
            *args,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        communicate = asyncio.create_task(proc.communicate())

        async def terminate_and_reap() -> None:
            # Terraform launches provider subprocesses. Terminate the complete
            # process group so no child can retain the remote state lock.
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGTERM)
            try:
                await asyncio.wait_for(asyncio.shield(communicate), timeout=10)
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(proc.pid, signal.SIGKILL)
                await communicate

        try:
            stdout, stderr = await asyncio.wait_for(
                asyncio.shield(communicate), timeout=timeout_seconds
            )
        except TimeoutError as exc:
            await terminate_and_reap()
            raise RuntimeError(
                f"Experiment Terraform {' '.join(args[:2])} timed out after {timeout_seconds}s"
            ) from exc
        except asyncio.CancelledError:
            # ARQ cancellation must not orphan Terraform or its provider
            # children. Reap them before propagating cancellation to ARQ.
            await asyncio.shield(terminate_and_reap())
            raise
        if proc.returncode != 0:
            raise RuntimeError(f"Experiment Terraform failed: {stderr.decode()[:4000]}")
        return stdout.decode()

    async def apply(
        self,
        experiment_id: str,
        instance_type: str,
        expires_at: str,
        safety_shutdown_minutes: int,
        instance_count: int = 1,
    ) -> ExperimentInfraResult:
        workdir = self._make_workdir()
        try:
            await self._run(
                ["init", *self._backend_config(experiment_id)],
                cwd=workdir,
                timeout_seconds=TERRAFORM_INIT_TIMEOUT_SECONDS,
            )
            await self._run(
                [
                    "apply",
                    "-auto-approve",
                    "-lock-timeout=5m",
                    f"-var=experiment_id={experiment_id}",
                    f"-var=instance_type={instance_type}",
                    f"-var=instance_count={instance_count}",
                    f"-var=aws_region={self._region}",
                    f"-var=expires_at={expires_at}",
                    f"-var=safety_shutdown_minutes={safety_shutdown_minutes}",
                ],
                cwd=workdir,
                timeout_seconds=TERRAFORM_APPLY_TIMEOUT_SECONDS,
            )
            outputs = json.loads(
                await self._run(
                    ["output", "-json"],
                    cwd=workdir,
                    timeout_seconds=TERRAFORM_OUTPUT_TIMEOUT_SECONDS,
                )
            )
            instance_ids = list(outputs["instance_ids"]["value"])
            if len(instance_ids) != instance_count:
                raise RuntimeError(
                    f"Terraform returned {len(instance_ids)} instances; expected {instance_count}"
                )
            return ExperimentInfraResult(instance_id=instance_ids[0], instance_ids=instance_ids)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    async def destroy(self, experiment_id: str) -> None:
        workdir = self._make_workdir()
        try:
            await self._run(
                ["init", *self._backend_config(experiment_id)],
                cwd=workdir,
                timeout_seconds=TERRAFORM_INIT_TIMEOUT_SECONDS,
            )
            await self._run(
                [
                    "destroy",
                    "-auto-approve",
                    "-lock-timeout=5m",
                    f"-var=experiment_id={experiment_id}",
                    f"-var=aws_region={self._region}",
                ],
                cwd=workdir,
                timeout_seconds=TERRAFORM_DESTROY_TIMEOUT_SECONDS,
            )
        finally:
            shutil.rmtree(workdir, ignore_errors=True)


class MockExperimentTerraform(ExperimentInfraRunner):
    async def apply(
        self,
        experiment_id: str,
        instance_type: str,
        expires_at: str,
        safety_shutdown_minutes: int,
        instance_count: int = 1,
    ) -> ExperimentInfraResult:
        logger.info(
            "[mock] experiment terraform apply id=%s size=%s count=%s expires=%s",
            experiment_id,
            instance_type,
            instance_count,
            expires_at,
        )
        await asyncio.sleep(0.01)
        instance_ids = [
            f"i-mock-experiment-{experiment_id[:8]}-{index + 1}"
            for index in range(instance_count)
        ]
        return ExperimentInfraResult(instance_id=instance_ids[0], instance_ids=instance_ids)

    async def destroy(self, experiment_id: str) -> None:
        logger.info("[mock] experiment terraform destroy id=%s", experiment_id)
        await asyncio.sleep(0.01)
