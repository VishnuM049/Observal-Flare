from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from arq import create_pool
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from server.api import audit_logs, auth, costs, deploy_sources, health, sites, webhooks, ws
from server.api.sites import set_arq_pool
from server.config import get_settings
from server.worker.settings import get_redis_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logger.info("Starting Flare (env=%s)", settings.flare_env.value)

    pool = await create_pool(get_redis_settings())
    set_arq_pool(pool)
    logger.info("ARQ pool connected")

    yield

    await pool.aclose()
    logger.info("Flare shutdown complete")


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="Flare",
        description="Observal instance provisioning platform",
        version="0.1.0",
        lifespan=lifespan,
    )

    origins = [settings.flare_base_url]
    if settings.is_local:
        origins.append("http://localhost:3000")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(auth.router)
    app.include_router(audit_logs.router)
    app.include_router(costs.router)
    app.include_router(sites.router)
    app.include_router(health.router)
    app.include_router(deploy_sources.router)
    app.include_router(webhooks.router)
    app.include_router(ws.router)

    return app


app = create_app()
