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

    async def run_command(self, instance_id: str, script: str, timeout_seconds: int = 600) -> CommandResult:
        # instance_id is the GCE instance name for GCP sites
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
                "--command", "bash /tmp/flare-remote-script.sh && rm -f /tmp/flare-remote-script.sh",
            ]

            # SCP the script
            logger.info("GCP: uploading script to %s", instance_id)
            proc = await asyncio.create_subprocess_exec(
                *scp_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                _, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
            except asyncio.TimeoutError:
                proc.kill()
                return CommandResult(status="timeout", output="SCP timed out uploading script")

            if proc.returncode != 0:
                return CommandResult(status="failed", output=f"SCP failed: {stderr.decode()}")

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
