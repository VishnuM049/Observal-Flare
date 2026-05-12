"""Mock implementations for local development (FLARE_ENV=local).

Simulates Terraform, SSM, and GitHub API with fake delays so the full
provisioning flow works end-to-end without AWS credentials.
"""
from __future__ import annotations

import asyncio
import logging

from server.services.github_service import GitHubClient
from server.ssm import CommandResult, SSMRunner
from server.terraform import TerraformResult, TerraformRunner

logger = logging.getLogger(__name__)


class MockTerraform(TerraformRunner):
    async def apply(self, site_name: str, instance_size: str) -> TerraformResult:
        logger.info("[mock] terraform apply for site=%s size=%s", site_name, instance_size)
        await asyncio.sleep(5)
        return TerraformResult(instance_id=f"i-mock-{site_name}", ip_address="127.0.0.1")

    async def destroy(self, site_name: str) -> None:
        logger.info("[mock] terraform destroy for site=%s", site_name)
        await asyncio.sleep(3)

    async def force_unlock(self, site_name: str, lock_id: str) -> None:
        logger.info("[mock] terraform force-unlock for site=%s lock=%s", site_name, lock_id)
        await asyncio.sleep(1)


class MockSSM(SSMRunner):
    async def run_command(self, instance_id: str, script: str, timeout_seconds: int = 600) -> CommandResult:
        logger.info("[mock] SSM command on %s (script: %d chars)", instance_id, len(script))
        await asyncio.sleep(3)
        return CommandResult(status="success", output="mock deploy complete")


class MockGitHubClient(GitHubClient):
    async def resolve_ref(self, deploy_type: str, ref: str) -> str:
        logger.info("[mock] resolve ref type=%s ref=%s", deploy_type, ref)
        return "abc123deadbeef0000000000000000000000cafe"

    async def get_commit_message(self, sha: str) -> str:
        return "mock commit message"

    async def check_org_membership(self, username: str) -> bool:
        logger.info("[mock] check org membership for %s", username)
        return True

    async def post_pr_comment(self, pr_number: int, body: str) -> None:
        logger.info("[mock] post PR comment on #%d: %s", pr_number, body[:100])
