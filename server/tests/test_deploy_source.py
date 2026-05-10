"""Test that each deploy type resolves to a SHA via mock GitHub."""
from __future__ import annotations

from server.mock import MockGitHubClient


async def test_resolve_branch():
    client = MockGitHubClient()
    sha = await client.resolve_ref("branch", "main")
    assert len(sha) == 40


async def test_resolve_commit():
    client = MockGitHubClient()
    sha = await client.resolve_ref("commit", "abc123")
    assert len(sha) == 40


async def test_resolve_pr():
    client = MockGitHubClient()
    sha = await client.resolve_ref("pr", "42")
    assert len(sha) == 40


async def test_resolve_tag():
    client = MockGitHubClient()
    sha = await client.resolve_ref("tag", "v0.4.0")
    assert len(sha) == 40


async def test_resolve_release():
    client = MockGitHubClient()
    sha = await client.resolve_ref("release", "v0.4.0")
    assert len(sha) == 40
