"""Tests for the WebSocket endpoint."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import WebSocketDisconnect

from server.api.ws import _validate_session, site_ws


def test_validate_session_rejects_missing_cookie():
    ws = MagicMock()
    ws.cookies = {}
    assert _validate_session(ws) is False


def test_validate_session_rejects_bad_token():
    ws = MagicMock()
    ws.cookies = {"session_token": "garbage"}
    assert _validate_session(ws) is False


def test_validate_session_accepts_valid_token():
    from itsdangerous import URLSafeTimedSerializer
    from server.config import get_settings
    serializer = URLSafeTimedSerializer(get_settings().secret_key)
    token = serializer.dumps("some-user-id")

    ws = MagicMock()
    ws.cookies = {"session_token": token}
    assert _validate_session(ws) is True


async def test_websocket_rejects_unauthenticated():
    """WebSocket accepts then closes with 4001 if no valid session cookie."""
    websocket = AsyncMock()
    websocket.cookies = {}
    websocket.close = AsyncMock()

    await site_ws(websocket, "some-site")

    websocket.accept.assert_called_once()
    websocket.close.assert_called_once_with(code=4001)


async def test_websocket_forwards_messages():
    """Authenticated WebSocket receives messages from Redis pubsub."""
    from itsdangerous import URLSafeTimedSerializer
    from server.config import get_settings
    serializer = URLSafeTimedSerializer(get_settings().secret_key)
    token = serializer.dumps("user-123")

    payload = json.dumps({"type": "status_change", "status": "deploying"})

    call_count = 0

    async def mock_get_message(ignore_subscribe_messages=True, timeout=5):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return {"type": "message", "data": payload.encode()}
        raise WebSocketDisconnect()

    pubsub = AsyncMock()
    pubsub.get_message = mock_get_message
    pubsub.subscribe = AsyncMock()
    pubsub.unsubscribe = AsyncMock()
    pubsub.aclose = AsyncMock()

    mock_redis = MagicMock()
    mock_redis.pubsub.return_value = pubsub
    mock_redis.aclose = AsyncMock()

    websocket = AsyncMock()
    websocket.cookies = {"session_token": token}
    websocket.accept = AsyncMock()
    websocket.send_text = AsyncMock()

    with patch("server.api.ws.aioredis.from_url", return_value=mock_redis):
        await site_ws(websocket, "fwd-test")

    websocket.accept.assert_called_once()
    websocket.send_text.assert_called_once_with(payload)
    pubsub.subscribe.assert_called_once_with("flare:site:fwd-test")
    pubsub.unsubscribe.assert_called_once_with("flare:site:fwd-test")


async def test_websocket_cleanup_on_send_failure():
    """If send_text raises (stale connection), loop exits and cleanup runs."""
    from itsdangerous import URLSafeTimedSerializer
    from server.config import get_settings
    serializer = URLSafeTimedSerializer(get_settings().secret_key)
    token = serializer.dumps("user-456")

    payload = json.dumps({"type": "status_change", "status": "running"})

    async def mock_get_message(ignore_subscribe_messages=True, timeout=5):
        return {"type": "message", "data": payload.encode()}

    pubsub = AsyncMock()
    pubsub.get_message = mock_get_message
    pubsub.subscribe = AsyncMock()
    pubsub.unsubscribe = AsyncMock()
    pubsub.aclose = AsyncMock()

    mock_redis = MagicMock()
    mock_redis.pubsub.return_value = pubsub
    mock_redis.aclose = AsyncMock()

    websocket = AsyncMock()
    websocket.cookies = {"session_token": token}
    websocket.accept = AsyncMock()
    websocket.send_text = AsyncMock(side_effect=RuntimeError("connection closed"))

    with patch("server.api.ws.aioredis.from_url", return_value=mock_redis):
        await site_ws(websocket, "stale-test")

    pubsub.unsubscribe.assert_called_once_with("flare:site:stale-test")
    pubsub.aclose.assert_called_once()
    mock_redis.aclose.assert_called_once()
