"""Tests for site name validation and duplicate detection."""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from server.models.site import DeployType
from server.models.user import User
from server.services.site_service import SiteError, create_site, validate_env_overrides, validate_site_name


def test_valid_names():
    for name in ["my-site", "pr-42", "a", "abc123", "a-b-c"]:
        validate_site_name(name)


def test_reject_uppercase():
    with pytest.raises(SiteError, match="lowercase"):
        validate_site_name("MyApp")


def test_reject_spaces():
    with pytest.raises(SiteError, match="lowercase"):
        validate_site_name("my site")


def test_reject_leading_hyphen():
    with pytest.raises(SiteError, match="lowercase"):
        validate_site_name("-bad")


def test_reject_trailing_hyphen():
    with pytest.raises(SiteError, match="lowercase"):
        validate_site_name("bad-")


def test_reject_empty():
    with pytest.raises(SiteError, match="lowercase"):
        validate_site_name("")


async def test_reject_duplicate_name(db: AsyncSession, admin_user: User):
    name = f"dup-{uuid.uuid4().hex[:6]}"
    await create_site(
        db,
        user=admin_user,
        name=name,
        deploy_type=DeployType.BRANCH,
        deploy_ref="main",
        requestor_email="test@test.local",
    )

    with pytest.raises(SiteError, match="already exists"):
        await create_site(
            db,
            user=admin_user,
            name=name,
            deploy_type=DeployType.BRANCH,
            deploy_ref="main",
            requestor_email="test@test.local",
        )


def test_env_valid_overrides():
    validate_env_overrides({"DEPLOYMENT_MODE": "cloud", "MY_FLAG": "true"})


def test_env_reject_reserved_key():
    with pytest.raises(SiteError, match="reserved"):
        validate_env_overrides({"DATABASE_URL": "postgres://evil"})


def test_env_reject_reserved_postgres_password():
    with pytest.raises(SiteError, match="reserved"):
        validate_env_overrides({"POSTGRES_PASSWORD": "leaked"})


def test_env_reject_invalid_key_format():
    with pytest.raises(SiteError, match="Invalid env var key"):
        validate_env_overrides({"BAD KEY": "val"})


def test_env_reject_key_starting_with_number():
    with pytest.raises(SiteError, match="Invalid env var key"):
        validate_env_overrides({"3INVALID": "val"})


def test_env_reject_newlines_in_value():
    with pytest.raises(SiteError, match="newlines"):
        validate_env_overrides({"GOOD_KEY": "line1\nline2"})


def test_env_reject_carriage_return():
    with pytest.raises(SiteError, match="newlines"):
        validate_env_overrides({"GOOD_KEY": "line1\rline2"})
