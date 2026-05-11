"""Test fixtures: async DB session against Docker Postgres, helper factories."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from server.database import Base
from server.models.site import DeployType, Site, SiteStatus, SleepMode
from server.models.user import User, UserRole

TEST_DB_URL = "postgresql+asyncpg://postgres:postgres@localhost:5432/flare_test"


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine(TEST_DB_URL, echo=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session

    async with engine.begin() as conn:
        await conn.execute(text(
            "TRUNCATE audit_logs, sites, users CASCADE"
        ))

    await engine.dispose()


@pytest_asyncio.fixture
async def admin_user(db: AsyncSession) -> User:
    user = User(email=f"admin-{uuid.uuid4().hex[:6]}@test.local", name="Test Admin", role=UserRole.ADMIN)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


@pytest_asyncio.fixture
async def running_site(db: AsyncSession, admin_user: User) -> Site:
    site = Site(
        name=f"test-{uuid.uuid4().hex[:6]}",
        domain="test.observal.io",
        deploy_type=DeployType.BRANCH,
        deploy_ref="main",
        requestor_email="admin@test.local",
        created_by=admin_user.id,
        instance_size="t3.large",
        status=SiteStatus.RUNNING,
        instance_id="i-mock-test",
        ip_address="127.0.0.1",
        auto_wipe_on_failure=True,
    )
    db.add(site)
    await db.commit()
    await db.refresh(site)
    return site
