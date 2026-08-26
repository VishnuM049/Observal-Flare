from __future__ import annotations

import abc
import asyncio
import json
import logging
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from server.config import get_settings

logger = logging.getLogger(__name__)
EXPERIMENT_TF_MODULE_DIR = Path("/app/infra/experiment")


@dataclass
class ExperimentInfraResult:
    instance_id: str


class ExperimentInfraRunner(abc.ABC):
    @abc.abstractmethod
    async def apply(
        self,
        experiment_id: str,
        instance_type: str,
        expires_at: str,
        safety_shutdown_minutes: int,
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

    async def _run(self, args: list[str], cwd: str) -> str:
        proc = await asyncio.create_subprocess_exec(
            "terraform",
            *args,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"Experiment Terraform failed: {stderr.decode()[:4000]}")
        return stdout.decode()

    async def apply(
        self,
        experiment_id: str,
        instance_type: str,
        expires_at: str,
        safety_shutdown_minutes: int,
    ) -> ExperimentInfraResult:
        workdir = self._make_workdir()
        try:
            await self._run(["init", *self._backend_config(experiment_id)], cwd=workdir)
            await self._run(
                [
                    "apply",
                    "-auto-approve",
                    f"-var=experiment_id={experiment_id}",
                    f"-var=instance_type={instance_type}",
                    f"-var=aws_region={self._region}",
                    f"-var=expires_at={expires_at}",
                    f"-var=safety_shutdown_minutes={safety_shutdown_minutes}",
                ],
                cwd=workdir,
            )
            outputs = json.loads(await self._run(["output", "-json"], cwd=workdir))
            return ExperimentInfraResult(instance_id=outputs["instance_id"]["value"])
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    async def destroy(self, experiment_id: str) -> None:
        workdir = self._make_workdir()
        try:
            await self._run(["init", *self._backend_config(experiment_id)], cwd=workdir)
            await self._run(
                [
                    "destroy",
                    "-auto-approve",
                    f"-var=experiment_id={experiment_id}",
                    f"-var=aws_region={self._region}",
                ],
                cwd=workdir,
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
    ) -> ExperimentInfraResult:
        logger.info(
            "[mock] experiment terraform apply id=%s size=%s expires=%s",
            experiment_id,
            instance_type,
            expires_at,
        )
        await asyncio.sleep(0.01)
        return ExperimentInfraResult(instance_id=f"i-mock-experiment-{experiment_id[:8]}")

    async def destroy(self, experiment_id: str) -> None:
        logger.info("[mock] experiment terraform destroy id=%s", experiment_id)
        await asyncio.sleep(0.01)
