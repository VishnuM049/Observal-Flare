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
            Parameters={"commands": [script]},
            TimeoutSeconds=timeout_seconds,
        ))
        command_id = resp["Command"]["CommandId"]

        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            await asyncio.sleep(10)
            result = await loop.run_in_executor(None, lambda: self._client.get_command_invocation(
                CommandId=command_id,
                InstanceId=instance_id,
            ))
            status = result["Status"]
            if status in ("Success", "Failed", "TimedOut", "Cancelled"):
                output = result.get("StandardOutputContent", "")
                if status != "Success":
                    output += "\n--- STDERR ---\n" + result.get("StandardErrorContent", "")
                return CommandResult(status=status.lower(), output=output)

        return CommandResult(status="timeout", output="Command timed out waiting for result")
