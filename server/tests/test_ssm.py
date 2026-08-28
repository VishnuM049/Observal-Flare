from unittest.mock import AsyncMock, patch

from server.ssm import (
    DELIVERY_TIMEOUT_SECONDS,
    RESULT_POLL_GRACE_SECONDS,
    RealSSM,
)


class RecordingSSMClient:
    def __init__(self) -> None:
        self.send_kwargs: dict = {}

    def send_command(self, **kwargs):
        self.send_kwargs = kwargs
        return {"Command": {"CommandId": "command-1"}}

    def get_command_invocation(self, **kwargs):
        return {
            "Status": "Success",
            "StandardOutputContent": "done",
            "StandardErrorContent": "",
        }


async def test_long_command_sets_document_execution_timeout_separately() -> None:
    client = RecordingSSMClient()
    runner = RealSSM()
    runner.__dict__["_client"] = client

    with patch("server.ssm.asyncio.sleep", new=AsyncMock()):
        result = await runner.run_command("i-long-running", "sleep 7200", timeout_seconds=7200)

    assert result.status == "success"
    assert client.send_kwargs["DocumentName"] == "AWS-RunShellScript"
    assert client.send_kwargs["Parameters"] == {
        "commands": ["sleep 7200"],
        "executionTimeout": ["7200"],
    }
    assert client.send_kwargs["TimeoutSeconds"] == DELIVERY_TIMEOUT_SECONDS == 600
    assert RESULT_POLL_GRACE_SECONDS >= 60
