"""WebSocket endpoint for real-time site status updates."""
from __future__ import annotations

import logging

import redis.asyncio as aioredis
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from itsdangerous import BadSignature, URLSafeTimedSerializer

from server.config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter()

SESSION_MAX_AGE = 86400 * 7


def _validate_session(websocket: WebSocket) -> bool:
    token = websocket.cookies.get("session_token") or websocket.query_params.get("token")
    if not token:
        return False
    try:
        serializer = URLSafeTimedSerializer(get_settings().secret_key)
        serializer.loads(token, max_age=SESSION_MAX_AGE)
        return True
    except BadSignature:
        return False


@router.websocket("/api/sites/ws/{site_id}")
async def site_ws(websocket: WebSocket, site_id: str) -> None:
    await websocket.accept()

    if not _validate_session(websocket):
        logger.warning("WebSocket auth failed for site %s (cookies: %s)", site_id, list(websocket.cookies.keys()))
        await websocket.close(code=4001)
        return

    r = aioredis.from_url(get_settings().redis_url)
    pubsub = r.pubsub()
    channel = f"flare:site:{site_id}"
    await pubsub.subscribe(channel)

    try:
        while True:
            msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=5)
            if msg is not None and msg["type"] == "message":
                try:
                    await websocket.send_text(msg["data"].decode())
                except Exception:
                    break
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.debug("WebSocket connection ended for site %s", site_id, exc_info=True)
    finally:
        await pubsub.unsubscribe(channel)
        await pubsub.aclose()
        await r.aclose()
