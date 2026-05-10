from fastapi import APIRouter
from sqlalchemy import text

from server.api.deps import DB
from server.config import get_settings

import redis.asyncio as aioredis

router = APIRouter(tags=["health"])


@router.get("/api/health")
async def health_check(db: DB):
    checks = {}

    try:
        await db.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as e:
        checks["database"] = f"error: {e}"

    try:
        r = aioredis.from_url(get_settings().redis_url)
        await r.ping()
        await r.aclose()
        checks["redis"] = "ok"
    except Exception as e:
        checks["redis"] = f"error: {e}"

    healthy = all(v == "ok" for v in checks.values())
    return {"status": "healthy" if healthy else "degraded", "checks": checks}
