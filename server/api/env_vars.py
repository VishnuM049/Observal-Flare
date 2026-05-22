from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException

from server.api.deps import CurrentUser
from server.config import get_settings
from server.mock import MockGitHubClient
from server.services.github_service import RealGitHubClient

import httpx

router = APIRouter(prefix="/api/env-vars", tags=["env-vars"])

_cache: dict[str, tuple[float, list[dict]]] = {}
CACHE_TTL = 300  # 5 minutes


def _parse_env_example(content: str) -> list[dict]:
    """Parse .env.example into a list of {key, default, description, section}."""
    results = []
    pending_comments: list[str] = []
    current_section = ""
    prev_was_blank = True

    for line in content.splitlines():
        stripped = line.strip()

        if not stripped:
            # Blank line after comments = those comments were a section header
            if pending_comments and prev_was_blank is False:
                current_section = " ".join(pending_comments)
                pending_comments = []
            else:
                pending_comments = []
            prev_was_blank = True
            continue

        if stripped.startswith("#"):
            clean = stripped.lstrip("# ").strip()
            if clean:
                pending_comments.append(clean)
            prev_was_blank = False
            continue

        if "=" in stripped:
            key, _, default = stripped.partition("=")
            key = key.strip()
            default = default.strip()

            # Comments immediately before a var = description for that var
            description = " ".join(pending_comments) if pending_comments else ""

            # If this is the first var after a blank+comment block and description
            # looks like a section header (short, ends with colon or parens), use it as section
            if description and prev_was_blank and len(pending_comments) == 1:
                candidate = pending_comments[0]
                if len(candidate) < 50 and (candidate.endswith(":") or candidate.endswith(")") or "(" in candidate):
                    current_section = candidate.rstrip(":")
                    description = ""

            results.append({
                "key": key,
                "default": default,
                "description": description,
                "section": current_section,
            })
            pending_comments = []
            prev_was_blank = False
        else:
            pending_comments = []
            prev_was_blank = False

    return results


@router.get("/known")
async def get_known_env_vars(user: CurrentUser):
    """Fetch and parse .env.example from the Observal repo."""
    cache_key = "env_vars"
    now = time.time()

    if cache_key in _cache:
        cached_at, data = _cache[cache_key]
        if now - cached_at < CACHE_TTL:
            return data

    settings = get_settings()

    if settings.use_mock_github:
        # In local mode, return a static example
        return _cache.setdefault(cache_key, (now, [
            {"key": "DEPLOYMENT_MODE", "default": "local", "description": "local (default) or enterprise", "section": "Core settings"},
            {"key": "DATA_RETENTION_DAYS", "default": "90", "description": "ClickHouse data retention (days). Set to 0 to disable TTL.", "section": "Core settings"},
        ]))[1]

    try:
        github_token = settings.github_token
        owner = settings.github_repo_owner
        repo = settings.github_repo_name
        url = f"https://api.github.com/repos/{owner}/{repo}/contents/.env.example"

        async with httpx.AsyncClient() as client:
            resp = await client.get(url, headers={
                "Authorization": f"Bearer {github_token}",
                "Accept": "application/vnd.github.raw+json",
            })
            resp.raise_for_status()
            content = resp.text

        data = _parse_env_example(content)
        _cache[cache_key] = (now, data)
        return data

    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch .env.example from GitHub: {e}")
