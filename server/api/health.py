import logging

from fastapi import APIRouter
from sqlalchemy import text

from server.api.deps import DB
from server.config import get_settings

import redis.asyncio as aioredis

logger = logging.getLogger(__name__)
router = APIRouter(tags=["health"])


@router.get("/api/health")
async def health_check(db: DB):
    checks = {}

    try:
        await db.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception:
        logger.exception("Health check: database unreachable")
        checks["database"] = "unavailable"

    try:
        r = aioredis.from_url(get_settings().redis_url)
        try:
            await r.ping()
            checks["redis"] = "ok"
        finally:
            await r.aclose()
    except Exception:
        logger.exception("Health check: redis unreachable")
        checks["redis"] = "unavailable"

    healthy = all(v == "ok" for v in checks.values())
    return {"status": "healthy" if healthy else "degraded", "checks": checks}
