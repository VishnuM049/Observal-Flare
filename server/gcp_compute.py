"""GCP Compute Engine instance lifecycle (start/stop/state)."""
from __future__ import annotations

import asyncio
import logging

from server.config import get_settings

logger = logging.getLogger(__name__)

# GCP state -> Flare normalized state
_STATE_MAP = {
    "RUNNING": "running",
    "STOPPED": "stopped",
    "TERMINATED": "stopped",
    "STAGING": "pending",
    "PROVISIONING": "pending",
    "STOPPING": "stopping",
    "SUSPENDING": "stopping",
    "SUSPENDED": "stopped",
}


class GCPCompute:
    """ComputeRunner implementation for GCP Compute Engine."""

    def __init__(self, project: str | None = None, zone: str | None = None):
        settings = get_settings()
        self._project = project or settings.gcp_project_id
        self._zone = zone or settings.gcp_zone

    def _get_client(self):
        from google.cloud import compute_v1
        return compute_v1.InstancesClient()

    async def get_state(self, instance_id: str) -> str:
        loop = asyncio.get_running_loop()
        client = self._get_client()
        instance = await loop.run_in_executor(
            None,
            lambda: client.get(project=self._project, zone=self._zone, instance=instance_id),
        )
        raw_status = instance.status
        return _STATE_MAP.get(raw_status, "stopped")

    async def start(self, instance_id: str, timeout_seconds: int = 300) -> None:
        loop = asyncio.get_running_loop()
        client = self._get_client()
        state = await self.get_state(instance_id)

        if state == "running":
            logger.info("GCE instance %s already running", instance_id)
            await self._wait_for_ssh(instance_id, timeout_seconds=180)
            return
        if state == "pending":
            logger.info("GCE instance %s is starting, waiting...", instance_id)
            await self._wait_for_state(instance_id, "running", timeout_seconds)
            await self._wait_for_ssh(instance_id, timeout_seconds=180)
            return
        if state == "stopping":
            logger.info("GCE instance %s is stopping, waiting before starting...", instance_id)
            await self._wait_for_state(instance_id, "stopped")

        await loop.run_in_executor(
            None,
            lambda: client.start(project=self._project, zone=self._zone, instance=instance_id),
        )
        logger.info("GCE start requested for %s, waiting for running state...", instance_id)
        await self._wait_for_state(instance_id, "running", timeout_seconds)
        logger.info("GCE instance %s is running, waiting for SSH readiness...", instance_id)
        await self._wait_for_ssh(instance_id, timeout_seconds=180)

    async def stop(self, instance_id: str) -> None:
        loop = asyncio.get_running_loop()
        client = self._get_client()
        state = await self.get_state(instance_id)

        if state == "stopped":
            logger.info("GCE instance %s already stopped", instance_id)
            return
        if state == "stopping":
            logger.info("GCE instance %s already stopping, waiting...", instance_id)
            await self._wait_for_state(instance_id, "stopped")
            return

        await loop.run_in_executor(
            None,
            lambda: client.stop(project=self._project, zone=self._zone, instance=instance_id),
        )
        logger.info("GCE stop requested for %s, waiting for stopped state...", instance_id)
        await self._wait_for_state(instance_id, "stopped")
        logger.info("GCE instance %s stopped", instance_id)

    async def _wait_for_state(self, instance_id: str, target: str, timeout_seconds: int = 300) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_seconds
        while loop.time() < deadline:
            state = await self.get_state(instance_id)
            if state == target:
                return
            await asyncio.sleep(5)
        raise RuntimeError(f"GCE instance {instance_id} did not reach {target} state within {timeout_seconds}s")

    async def _ensure_auth(self) -> None:
        proc = await asyncio.create_subprocess_exec(
            "gcloud", "auth", "login",
            "--cred-file=/etc/flare/gcp-credentials.json",
            "--quiet",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()

    async def _wait_for_ssh(self, instance_id: str, timeout_seconds: int = 180) -> None:
        """Wait until gcloud compute ssh via IAP is reachable."""
        await self._ensure_auth()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_seconds
        while loop.time() < deadline:
            proc = await asyncio.create_subprocess_exec(
                "gcloud", "compute", "ssh", instance_id,
                f"--project={self._project}",
                f"--zone={self._zone}",
                "--tunnel-through-iap",
                "--quiet",
                "--command", "echo ready",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                await asyncio.wait_for(proc.communicate(), timeout=15)
            except asyncio.TimeoutError:
                proc.kill()
                await asyncio.sleep(5)
                continue
            if proc.returncode == 0:
                logger.info("SSH via IAP ready for %s", instance_id)
                return
            await asyncio.sleep(5)
        raise RuntimeError(f"SSH via IAP on {instance_id} did not become available within {timeout_seconds}s")
