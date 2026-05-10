from fastapi import APIRouter, HTTPException, Query

from server.config import get_settings
from server.mock import MockGitHubClient
from server.services.github_service import RealGitHubClient

router = APIRouter(prefix="/api/deploy-sources", tags=["deploy-sources"])


@router.get("/validate")
async def validate_deploy_source(type: str = Query(...), ref: str = Query(...)):
    settings = get_settings()
    github = MockGitHubClient() if settings.is_local else RealGitHubClient()

    try:
        sha = await github.resolve_ref(type, ref)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not resolve {type}/{ref}: {e}")

    return {"type": type, "ref": ref, "resolved_sha": sha, "valid": True}
