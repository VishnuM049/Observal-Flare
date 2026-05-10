from __future__ import annotations

import abc
import asyncio
import json
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from server.config import get_settings

TF_MODULE_DIR = Path("/app/infra/site")


@dataclass
class TerraformResult:
    instance_id: str
    ip_address: str


class TerraformRunner(abc.ABC):
    @abc.abstractmethod
    async def apply(self, site_name: str, instance_size: str) -> TerraformResult:
        """Run terraform apply for a site."""

    @abc.abstractmethod
    async def destroy(self, site_name: str) -> None:
        """Run terraform destroy for a site."""

    @abc.abstractmethod
    async def force_unlock(self, site_name: str, lock_id: str) -> None:
        """Force-unlock a stuck Terraform state."""


class RealTerraform(TerraformRunner):
    def __init__(self) -> None:
        settings = get_settings()
        self._bucket = settings.terraform_state_bucket
        self._lock_table = settings.terraform_lock_table
        self._region = settings.aws_region
        self._zone_id = settings.route53_zone_id
        self._base_domain = settings.site_base_domain

    def _backend_config(self, site_name: str) -> list[str]:
        return [
            f"-backend-config=bucket={self._bucket}",
            f"-backend-config=key=sites/{site_name}/terraform.tfstate",
            f"-backend-config=region={self._region}",
            f"-backend-config=dynamodb_table={self._lock_table}",
        ]

    def _make_workdir(self) -> str:
        """Copy the Terraform module to a temp dir so concurrent operations don't conflict."""
        workdir = tempfile.mkdtemp(prefix="flare-tf-")
        shutil.copytree(TF_MODULE_DIR, workdir, dirs_exist_ok=True)
        return workdir

    async def _run(self, args: list[str], cwd: str) -> str:
        proc = await asyncio.create_subprocess_exec(
            "terraform", *args,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"Terraform failed: {stderr.decode()}")
        return stdout.decode()

    async def apply(self, site_name: str, instance_size: str) -> TerraformResult:
        workdir = self._make_workdir()
        try:
            await self._run(["init", *self._backend_config(site_name)], cwd=workdir)
            await self._run([
                "apply", "-auto-approve",
                f"-var=site_name={site_name}",
                f"-var=instance_size={instance_size}",
                f"-var=route53_zone_id={self._zone_id}",
                f"-var=base_domain={self._base_domain}",
            ], cwd=workdir)
            output_raw = await self._run(["output", "-json"], cwd=workdir)
            outputs = json.loads(output_raw)
            return TerraformResult(
                instance_id=outputs["instance_id"]["value"],
                ip_address=outputs["public_ip"]["value"],
            )
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    async def destroy(self, site_name: str) -> None:
        workdir = self._make_workdir()
        try:
            await self._run(["init", *self._backend_config(site_name)], cwd=workdir)
            await self._run(["destroy", "-auto-approve"], cwd=workdir)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    async def force_unlock(self, site_name: str, lock_id: str) -> None:
        workdir = self._make_workdir()
        try:
            await self._run(["init", *self._backend_config(site_name)], cwd=workdir)
            await self._run(["force-unlock", "-force", lock_id], cwd=workdir)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
