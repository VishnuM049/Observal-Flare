"""Tests for the Redis pub/sub event publisher."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch, PropertyMock

import server.events as events_module
from server.events import publish_site_event


async def test_publish_site_event():
    """Verify publish() is called with the correct channel and JSON payload."""
    mock_redis = AsyncMock()
    mock_pool = AsyncMock()

    events_module._pool = None
    with patch("server.events.aioredis.ConnectionPool.from_url", return_value=mock_pool):
        with patch("server.events.aioredis.Redis", return_value=mock_redis):
            await publish_site_event("abc-123", "status_change", status="running", message="Site is live")

    mock_redis.publish.assert_called_once()
    channel, raw = mock_redis.publish.call_args.args
    assert channel == "flare:site:abc-123"
    payload = json.loads(raw)
    assert payload["type"] == "status_change"
    assert payload["status"] == "running"
    assert payload["message"] == "Site is live"
    assert "timestamp" in payload
    events_module._pool = None


async def test_publish_failure_does_not_raise():
    """If Redis is unavailable, publish_site_event logs but does not raise."""
    mock_redis = AsyncMock()
    mock_redis.publish.side_effect = ConnectionError("Redis down")

    events_module._pool = None
    with patch("server.events.aioredis.ConnectionPool.from_url", return_value=AsyncMock()):
        with patch("server.events.aioredis.Redis", return_value=mock_redis):
            await publish_site_event("abc-123", "status_change", status="running")
    events_module._pool = None


async def test_publish_event_payload_shape():
    """Payload always has type, status, message, and timestamp fields."""
    mock_redis = AsyncMock()

    events_module._pool = None
    with patch("server.events.aioredis.ConnectionPool.from_url", return_value=AsyncMock()):
        with patch("server.events.aioredis.Redis", return_value=mock_redis):
            await publish_site_event("x", "stage_progress", message="Resolving SHA...")

    raw = mock_redis.publish.call_args.args[1]
    payload = json.loads(raw)
    assert set(payload.keys()) == {"type", "status", "message", "timestamp"}
    assert payload["type"] == "stage_progress"
    assert payload["status"] is None
    assert payload["message"] == "Resolving SHA..."
    events_module._pool = None


async def test_connection_pool_reused():
    """The connection pool is created once and reused across calls."""
    mock_redis = AsyncMock()
    mock_pool = AsyncMock()

    events_module._pool = None
    with patch("server.events.aioredis.ConnectionPool.from_url", return_value=mock_pool) as from_url:
        with patch("server.events.aioredis.Redis", return_value=mock_redis):
            await publish_site_event("a", "status_change")
            await publish_site_event("b", "status_change")

    from_url.assert_called_once()
    assert mock_redis.publish.call_count == 2
    events_module._pool = None
