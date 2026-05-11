"""Redis pub/sub event publisher for real-time site status updates."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import redis.asyncio as aioredis

from server.config import get_settings

logger = logging.getLogger(__name__)

_pool: aioredis.ConnectionPool | None = None


def _get_pool() -> aioredis.ConnectionPool:
    global _pool
    if _pool is None:
        _pool = aioredis.ConnectionPool.from_url(get_settings().redis_url)
    return _pool


async def publish_site_event(
    site_id: str,
    event_type: str,
    status: str | None = None,
    message: str = "",
) -> None:
    """Publish a site event to Redis pub/sub. Never raises."""
    try:
        r = aioredis.Redis(connection_pool=_get_pool())
        payload = {
            "type": event_type,
            "status": status,
            "message": message,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        await r.publish(f"flare:site:{site_id}", json.dumps(payload))
    except Exception:
        logger.warning("Failed to publish event for site %s", site_id, exc_info=True)
