"""GCP Terraform runner — provisions GCE instances via infra/site-gcp/ module."""
from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
from pathlib import Path

from server.config import get_settings
from server.terraform import TerraformResult, TerraformRunner

TF_MODULE_DIR_GCP = Path("/app/infra/site-gcp")


class GCPTerraform(TerraformRunner):
    def __init__(self) -> None:
        settings = get_settings()
        self._bucket = settings.gcp_terraform_state_bucket
        self._project = settings.gcp_project_id
        self._region = settings.gcp_region
        self._zone = settings.gcp_zone
        self._route53_zone_id = settings.route53_zone_id
        self._base_domain = settings.site_base_domain

    def _backend_config(self, site_name: str) -> list[str]:
        return [
            f"-backend-config=bucket={self._bucket}",
            f"-backend-config=prefix=sites/{site_name}",
        ]

    def _make_workdir(self) -> str:
        workdir = tempfile.mkdtemp(prefix="flare-tf-gcp-")
        shutil.copytree(TF_MODULE_DIR_GCP, workdir, dirs_exist_ok=True)
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
            raise RuntimeError(f"Terraform (GCP) failed: {stderr.decode()}")
        return stdout.decode()

    async def apply(self, site_name: str, instance_size: str) -> TerraformResult:
        workdir = self._make_workdir()
        try:
            await self._run(["init", *self._backend_config(site_name)], cwd=workdir)
            await self._run([
                "apply", "-auto-approve",
                f"-var=site_name={site_name}",
                f"-var=machine_type={instance_size}",
                f"-var=project={self._project}",
                f"-var=region={self._region}",
                f"-var=zone={self._zone}",
                f"-var=route53_zone_id={self._route53_zone_id}",
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
            await self._run([
                "destroy", "-auto-approve",
                f"-var=site_name={site_name}",
                f"-var=project={self._project}",
                f"-var=region={self._region}",
                f"-var=zone={self._zone}",
                f"-var=route53_zone_id={self._route53_zone_id}",
                f"-var=base_domain={self._base_domain}",
            ], cwd=workdir)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    async def force_unlock(self, site_name: str, lock_id: str) -> None:
        workdir = self._make_workdir()
        try:
            await self._run(["init", *self._backend_config(site_name)], cwd=workdir)
            await self._run(["force-unlock", "-force", lock_id], cwd=workdir)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
