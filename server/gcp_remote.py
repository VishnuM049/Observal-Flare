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
    """Execute commands on GCE instances via gcloud compute ssh with IAP tunneling.

    Uses a fire-and-forget pattern: SCP the script, start it via nohup in the
    background, then poll for a completion marker. This avoids IAP tunnel
    disconnects during long-running builds.
    """

    def __init__(self, project: str | None = None, zone: str | None = None):
        settings = get_settings()
        self._project = project or settings.gcp_project_id
        self._zone = zone or settings.gcp_zone

    async def _ensure_auth(self) -> None:
        proc = await asyncio.create_subprocess_exec(
            "gcloud", "auth", "login",
            "--cred-file=/etc/flare/gcp-credentials.json",
            "--quiet",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()

    async def _ssh(self, instance_id: str, command: str, timeout: int = 60) -> tuple[int, str, str]:
        """Run a short SSH command. Returns (returncode, stdout, stderr)."""
        proc = await asyncio.create_subprocess_exec(
            "gcloud", "compute", "ssh", instance_id,
            f"--project={self._project}",
            f"--zone={self._zone}",
            "--tunnel-through-iap",
            "--quiet",
            "--command", command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            return -1, "", "SSH command timed out"
        return proc.returncode, stdout.decode(), stderr.decode()

    async def run_command(self, instance_id: str, script: str, timeout_seconds: int = 1200) -> CommandResult:
        await self._ensure_auth()
        script_file = None
        try:
            script_file = tempfile.NamedTemporaryFile(mode="w", suffix=".sh", delete=False, prefix="flare-gcp-")
            script_file.write(script)
            script_file.flush()
            script_path = script_file.name
            script_file.close()

            # SCP the script (retry up to 3 times)
            scp_cmd = [
                "gcloud", "compute", "scp",
                script_path,
                f"{instance_id}:/tmp/flare-remote-script.sh",
                f"--project={self._project}",
                f"--zone={self._zone}",
                "--tunnel-through-iap",
                "--quiet",
            ]

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

            # Launch script in background via nohup, write exit code to marker file
            launch_cmd = (
                "sudo bash -c '"
                "nohup bash -c \"bash /tmp/flare-remote-script.sh; echo \\$? > /tmp/flare-script.exit\" "
                ">/tmp/flare-script-output.log 2>&1 &"
                " echo $! > /tmp/flare-script.pid"
                "'"
            )
            logger.info("GCP: launching script in background on %s", instance_id)
            rc, _, err = await self._ssh(instance_id, launch_cmd, timeout=30)
            if rc != 0:
                return CommandResult(status="failed", output=f"Failed to launch script: {err}")

            # Poll for completion
            logger.info("GCP: polling for script completion on %s (timeout %ds)", instance_id, timeout_seconds)
            deadline = asyncio.get_running_loop().time() + timeout_seconds
            while asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(15)
                rc, stdout, _ = await self._ssh(
                    instance_id,
                    "sudo bash -c '"
                    "if [ -f /tmp/flare-script.exit ]; then echo DONE:$(cat /tmp/flare-script.exit); "
                    "elif ! kill -0 $(cat /tmp/flare-script.pid 2>/dev/null) 2>/dev/null; then echo DONE:0; "
                    "else echo RUNNING; fi'",
                    timeout=30,
                )
                status = stdout.strip()
                if status.startswith("DONE:"):
                    exit_code = int(status.split(":")[1])
                    # Fetch output
                    _, output, _ = await self._ssh(
                        instance_id,
                        "sudo cat /tmp/flare-script-output.log 2>/dev/null; "
                        "sudo rm -f /tmp/flare-remote-script.sh /tmp/flare-script.pid /tmp/flare-script-output.log /tmp/flare-script.exit",
                        timeout=30,
                    )
                    if exit_code == 0:
                        return CommandResult(status="success", output=output)
                    return CommandResult(status="failed", output=output)
                elif status == "MISSING":
                    return CommandResult(status="failed", output="Script PID file missing — launch may have failed")
                # RUNNING — continue polling

            # Timeout — fetch whatever output exists
            _, output, _ = await self._ssh(
                instance_id,
                "sudo cat /tmp/flare-script-output.log 2>/dev/null | tail -50",
                timeout=30,
            )
            return CommandResult(status="timeout", output=f"Command timed out after {timeout_seconds}s. Last output:\n{output}")

        finally:
            if script_file:
                Path(script_file.name).unlink(missing_ok=True)
