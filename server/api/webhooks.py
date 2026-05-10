"""GitHub webhook receiver — Phase 2 implementation, endpoint registered in Phase 1."""
from __future__ import annotations

import hashlib
import hmac
import logging

from fastapi import APIRouter, Header, HTTPException, Request

from server.config import get_settings

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])
logger = logging.getLogger(__name__)


def _verify_signature(payload: bytes, signature: str | None, secret: str) -> bool:
    if not signature or not secret:
        return False
    expected = "sha256=" + hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


@router.post("/github")
async def github_webhook(
    request: Request,
    x_hub_signature_256: str | None = Header(None),
    x_github_event: str | None = Header(None),
):
    settings = get_settings()
    body = await request.body()

    # Signature verification (skip in local mode if no secret configured)
    webhook_secret = getattr(settings, "github_webhook_secret", "")
    if webhook_secret and not _verify_signature(body, x_hub_signature_256, webhook_secret):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    logger.info("Received GitHub webhook: event=%s", x_github_event)

    # Phase 2: auto-update on push, auto-teardown on PR close
    # For now, just acknowledge the webhook
    return {"received": True, "event": x_github_event}
