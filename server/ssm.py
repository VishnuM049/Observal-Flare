from __future__ import annotations

import abc
import asyncio
import time
from dataclasses import dataclass
from functools import cached_property

import boto3

from server.config import get_settings


@dataclass
class CommandResult:
    status: str
    output: str
    stderr: str = ""


# SendCommand.TimeoutSeconds controls how long SSM will try to deliver a
# command to an instance. AWS-RunShellScript's executionTimeout parameter is
# the separate limit for the script after delivery.
DELIVERY_TIMEOUT_SECONDS = 600
RESULT_POLL_GRACE_SECONDS = 60


class SSMRunner(abc.ABC):
    @abc.abstractmethod
    async def run_command(self, instance_id: str, script: str, timeout_seconds: int = 600) -> CommandResult:
        """Execute a shell script on a remote EC2 instance via SSM."""


class RealSSM(SSMRunner):
    @cached_property
    def _client(self):
        settings = get_settings()
        kwargs: dict = {"region_name": settings.aws_region}
        if settings.aws_access_key_id:
            kwargs["aws_access_key_id"] = settings.aws_access_key_id
            kwargs["aws_secret_access_key"] = settings.aws_secret_access_key
        return boto3.client("ssm", **kwargs)

    async def run_command(self, instance_id: str, script: str, timeout_seconds: int = 600) -> CommandResult:
        loop = asyncio.get_event_loop()
        resp = await loop.run_in_executor(None, lambda: self._client.send_command(
            InstanceIds=[instance_id],
            DocumentName="AWS-RunShellScript",
            Parameters={
                "commands": [script],
                "executionTimeout": [str(timeout_seconds)],
            },
            TimeoutSeconds=DELIVERY_TIMEOUT_SECONDS,
        ))
        command_id = resp["Command"]["CommandId"]

        deadline = time.time() + timeout_seconds + RESULT_POLL_GRACE_SECONDS
        while time.time() < deadline:
            await asyncio.sleep(10)
            result = await loop.run_in_executor(None, lambda: self._client.get_command_invocation(
                CommandId=command_id,
                InstanceId=instance_id,
            ))
            status = result["Status"]
            if status in ("Success", "Failed", "TimedOut", "Cancelled"):
                stdout = result.get("StandardOutputContent", "")
                stderr = result.get("StandardErrorContent", "")
                output = stdout
                if status != "Success":
                    output += "\n--- STDERR ---\n" + stderr
                return CommandResult(status=status.lower(), output=output, stderr=stderr)

        return CommandResult(status="timeout", output="Command timed out waiting for result")
