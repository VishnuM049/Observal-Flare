"""GitHub webhook receiver — auto-update on push, auto-teardown on PR close."""
from __future__ import annotations

import hashlib
import hmac
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Header, HTTPException, Request
from sqlalchemy import select

from server.api.deps import DB
from server.api.sites import _get_pool
from server.config import get_settings
from server.models.site import DeployType, Site, SiteStatus
from server.notifications.email import send_site_notification

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])
logger = logging.getLogger(__name__)

AUTO_UPDATE_STATUSES = {SiteStatus.RUNNING, SiteStatus.SLEEPING}
TEARDOWN_STATUSES = {SiteStatus.RUNNING, SiteStatus.STOPPED, SiteStatus.SLEEPING}


def _verify_signature(payload: bytes, signature: str | None, secret: str) -> bool:
    if not signature or not secret:
        return False
    expected = "sha256=" + hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


async def _handle_push(db: DB, payload: dict) -> dict:
    """Push event — find matching branch sites with auto_update and enqueue redeploy."""
    ref = payload.get("ref", "")
    if not ref.startswith("refs/heads/"):
        return {"matched": 0, "reason": "not a branch push"}

    branch = ref.removeprefix("refs/heads/")
    head_sha = payload.get("after", "")

    result = await db.execute(
        select(Site)
        .where(
            Site.deploy_type == DeployType.BRANCH,
            Site.deploy_ref == branch,
            Site.auto_update.is_(True),
            Site.status.in_([s.value for s in AUTO_UPDATE_STATUSES]),
        )
        .with_for_update()
    )
    sites = list(result.scalars().all())

    pool = _get_pool()
    for site in sites:
        await pool.enqueue_job("redeploy_site", str(site.id), _job_id=f"redeploy-{site.id}")
        logger.info("Auto-update: enqueued redeploy for site %s (branch %s, sha %s)", site.name, branch, head_sha[:8])

    return {"matched": len(sites), "branch": branch, "sha": head_sha[:8]}


async def _handle_pull_request(db: DB, payload: dict) -> dict:
    """Pull request events — auto-update on synchronize, auto-teardown on close."""
    action = payload.get("action", "")
    pr = payload.get("pull_request", {})
    pr_number = str(pr.get("number", ""))
    logger.info("Pull request webhook: action=%s pr=#%s", action, pr_number)

    if not pr_number:
        return {"matched": 0, "reason": "no PR number"}

    if action == "synchronize":
        result = await db.execute(
            select(Site)
            .where(
                Site.deploy_type == DeployType.PR,
                Site.deploy_ref == pr_number,
                Site.auto_update.is_(True),
                Site.status.in_([s.value for s in AUTO_UPDATE_STATUSES]),
            )
            .with_for_update()
        )
        sites = list(result.scalars().all())

        pool = _get_pool()
        for site in sites:
            await pool.enqueue_job("redeploy_site", str(site.id), _job_id=f"redeploy-{site.id}")
            logger.info("Auto-update: enqueued redeploy for site %s (PR #%s)", site.name, pr_number)

        return {"action": "synchronize", "matched": len(sites), "pr": pr_number}

    elif action == "closed":
        result = await db.execute(
            select(Site)
            .where(
                Site.deploy_type == DeployType.PR,
                Site.deploy_ref == pr_number,
                Site.status.in_([s.value for s in TEARDOWN_STATUSES]),
            )
        )
        sites = list(result.scalars().all())

        for site in sites:
            site.scheduled_destroy_at = datetime.now(timezone.utc) + timedelta(hours=24)
            logger.info("Auto-teardown: site %s scheduled for destruction in 24h (PR #%s closed)", site.name, pr_number)

        await db.commit()

        for site in sites:
            await send_site_notification(site, "pr_closed")

        return {"action": "closed", "matched": len(sites), "pr": pr_number}

    return {"action": action, "matched": 0, "reason": "unhandled action"}


@router.post("/github")
async def github_webhook(
    request: Request,
    db: DB,
    x_hub_signature_256: str | None = Header(None),
    x_github_event: str | None = Header(None),
):
    settings = get_settings()
    body = await request.body()

    if settings.github_webhook_secret:
        if not _verify_signature(body, x_hub_signature_256, settings.github_webhook_secret):
            raise HTTPException(status_code=401, detail="Invalid webhook signature")
    elif not settings.is_local:
        raise HTTPException(status_code=500, detail="Webhook secret not configured")

    payload = await request.json()
    logger.info("Received GitHub webhook: event=%s", x_github_event)

    if x_github_event == "push":
        result = await _handle_push(db, payload)
    elif x_github_event == "pull_request":
        result = await _handle_pull_request(db, payload)
    else:
        result = {"event": x_github_event, "ignored": True}

    return {"received": True, "event": x_github_event, **result}
