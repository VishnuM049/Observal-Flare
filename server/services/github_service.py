from __future__ import annotations

import abc

import httpx

from server.config import get_settings


class GitHubClient(abc.ABC):
    @abc.abstractmethod
    async def resolve_ref(self, deploy_type: str, ref: str) -> str:
        """Resolve a deploy reference to a commit SHA."""

    @abc.abstractmethod
    async def check_org_membership(self, username: str) -> bool:
        """Check if a GitHub user is a member of the org."""

    @abc.abstractmethod
    async def get_commit_message(self, sha: str) -> str:
        """Get the commit message for a SHA."""

    @abc.abstractmethod
    async def post_pr_comment(self, pr_number: int, body: str) -> None:
        """Post or update a comment on a PR."""


class RealGitHubClient(GitHubClient):
    def __init__(self) -> None:
        settings = get_settings()
        self._token = settings.github_token
        self._owner = settings.github_repo_owner
        self._repo = settings.github_repo_name
        self._org = settings.github_org
        self._base = "https://api.github.com"

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}", "Accept": "application/vnd.github+json"}

    async def resolve_ref(self, deploy_type: str, ref: str) -> str:
        async with httpx.AsyncClient(headers=self._headers) as client:
            match deploy_type:
                case "branch":
                    resp = await client.get(f"{self._base}/repos/{self._owner}/{self._repo}/branches/{ref}")
                    resp.raise_for_status()
                    return resp.json()["commit"]["sha"]
                case "pr":
                    resp = await client.get(f"{self._base}/repos/{self._owner}/{self._repo}/pulls/{ref}")
                    resp.raise_for_status()
                    return resp.json()["head"]["sha"]
                case "commit":
                    resp = await client.get(f"{self._base}/repos/{self._owner}/{self._repo}/commits/{ref}")
                    resp.raise_for_status()
                    return resp.json()["sha"]
                case "tag":
                    resp = await client.get(f"{self._base}/repos/{self._owner}/{self._repo}/git/ref/tags/{ref}")
                    resp.raise_for_status()
                    return resp.json()["object"]["sha"]
                case "release":
                    resp = await client.get(f"{self._base}/repos/{self._owner}/{self._repo}/releases/tags/{ref}")
                    resp.raise_for_status()
                    return resp.json()["target_commitish"]
                case _:
                    raise ValueError(f"Unknown deploy type: {deploy_type}")

    async def get_commit_message(self, sha: str) -> str:
        async with httpx.AsyncClient(headers=self._headers) as client:
            resp = await client.get(f"{self._base}/repos/{self._owner}/{self._repo}/commits/{sha}")
            resp.raise_for_status()
            return resp.json()["commit"]["message"].split("\n")[0]

    async def check_org_membership(self, username: str) -> bool:
        async with httpx.AsyncClient(headers=self._headers) as client:
            resp = await client.get(f"{self._base}/orgs/{self._org}/members/{username}")
            return resp.status_code == 204

    async def post_pr_comment(self, pr_number: int, body: str) -> None:
        async with httpx.AsyncClient(headers=self._headers) as client:
            resp = await client.post(
                f"{self._base}/repos/{self._owner}/{self._repo}/issues/{pr_number}/comments",
                json={"body": body},
            )
            resp.raise_for_status()
