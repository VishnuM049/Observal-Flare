"""GCP remote command execution via gcloud compute ssh (IAP tunnel)."""
from __future__ import annotations

import asyncio
import logging
import tempfile
from pathlib import Path

from server.config import get_settings
from server.ssm import CommandResult, SSMRunner

logger = logging.getLogger(__name__)


class GCPRemoteRunner(SSMRunner):
    """Execute commands on GCE instances via gcloud compute ssh with IAP tunneling."""

    def __init__(self, project: str | None = None, zone: str | None = None):
        settings = get_settings()
        self._project = project or settings.gcp_project_id
        self._zone = zone or settings.gcp_zone

    async def _ensure_auth(self) -> None:
        """Activate gcloud credentials (workaround for gcloud SSH crash with env-var-only auth)."""
        proc = await asyncio.create_subprocess_exec(
            "gcloud", "auth", "login",
            "--cred-file=/etc/flare/gcp-credentials.json",
            "--quiet",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()

    async def run_command(self, instance_id: str, script: str, timeout_seconds: int = 600) -> CommandResult:
        # instance_id is the GCE instance name for GCP sites
        await self._ensure_auth()
        script_file = None
        try:
            # Write script to temp file
            script_file = tempfile.NamedTemporaryFile(mode="w", suffix=".sh", delete=False, prefix="flare-gcp-")
            script_file.write(script)
            script_file.flush()
            script_path = script_file.name
            script_file.close()

            cmd = [
                "gcloud", "compute", "ssh",
                instance_id,
                f"--project={self._project}",
                f"--zone={self._zone}",
                "--tunnel-through-iap",
                "--quiet",
                "--no-user-output-enabled",
                "--command", f"bash {script_path}",
            ]

            # SCP the script to the instance first, then execute
            scp_cmd = [
                "gcloud", "compute", "scp",
                script_path,
                f"{instance_id}:/tmp/flare-remote-script.sh",
                f"--project={self._project}",
                f"--zone={self._zone}",
                "--tunnel-through-iap",
                "--quiet",
            ]

            exec_cmd = [
                "gcloud", "compute", "ssh",
                instance_id,
                f"--project={self._project}",
                f"--zone={self._zone}",
                "--tunnel-through-iap",
                "--quiet",
                "--command", "sudo bash /tmp/flare-remote-script.sh && rm -f /tmp/flare-remote-script.sh",
            ]

            # SCP the script (retry up to 3 times — IAP tunnel can be flaky on new instances)
            logger.info("GCP: uploading script to %s", instance_id)
            scp_error = ""
            for attempt in range(3):
                proc = await asyncio.create_subprocess_exec(
                    *scp_cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                try:
                    _, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
                except asyncio.TimeoutError:
                    proc.kill()
                    scp_error = "SCP timed out uploading script"
                    await asyncio.sleep(10)
                    continue

                if proc.returncode == 0:
                    break
                scp_error = f"SCP failed: {stderr.decode()}"
                if attempt < 2:
                    logger.info("GCP: SCP attempt %d failed, retrying in 10s...", attempt + 1)
                    await asyncio.sleep(10)
            else:
                return CommandResult(status="failed", output=scp_error)

            # Execute the script
            logger.info("GCP: executing script on %s", instance_id)
            proc = await asyncio.create_subprocess_exec(
                *exec_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_seconds)
            except asyncio.TimeoutError:
                proc.kill()
                return CommandResult(status="timeout", output="Command timed out")

            output = stdout.decode()
            if proc.returncode != 0:
                output += "\n--- STDERR ---\n" + stderr.decode()
                return CommandResult(status="failed", output=output)

            return CommandResult(status="success", output=output)

        finally:
            if script_file:
                Path(script_file.name).unlink(missing_ok=True)
