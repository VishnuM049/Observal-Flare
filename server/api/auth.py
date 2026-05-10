from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel, EmailStr

from server.api.deps import DB, CurrentUser, create_session_token
from server.config import get_settings
from server.models.user import User, UserRole
from server.services.github_service import RealGitHubClient
from server.services.invite_service import InviteError, redeem_invite, validate_invite_token
from server.mock import MockGitHubClient

import httpx
from sqlalchemy import select

router = APIRouter(prefix="/api/auth", tags=["auth"])


class GitHubLoginRequest(BaseModel):
    code: str


class InviteSignupRequest(BaseModel):
    name: str
    email: EmailStr


class UserResponse(BaseModel):
    id: uuid.UUID
    email: str
    name: str
    role: str
    is_active: bool

    class Config:
        from_attributes = True


@router.post("/login")
async def github_login(body: GitHubLoginRequest, response: Response, db: DB):
    settings = get_settings()

    async with httpx.AsyncClient() as client:
        token_resp = await client.post(
            "https://github.com/login/oauth/access_token",
            json={"client_id": settings.github_client_id, "client_secret": settings.github_client_secret, "code": body.code},
            headers={"Accept": "application/json"},
        )
        token_data = token_resp.json()
        access_token = token_data.get("access_token")
        if not access_token:
            raise HTTPException(status_code=400, detail="Failed to exchange OAuth code")

        user_resp = await client.get(
            "https://api.github.com/user",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        gh_user = user_resp.json()

    github = MockGitHubClient() if settings.is_local else RealGitHubClient()
    is_member = await github.check_org_membership(gh_user["login"])
    if not is_member:
        raise HTTPException(status_code=403, detail=f"User is not a member of {settings.github_org}")

    result = await db.execute(select(User).where(User.email == gh_user.get("email", f"{gh_user['login']}@github.com")))
    user = result.scalar_one_or_none()

    if user is None:
        user = User(
            email=gh_user.get("email") or f"{gh_user['login']}@github.com",
            name=gh_user.get("name") or gh_user["login"],
            role=UserRole.ADMIN,
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)

    user.last_login_at = datetime.utcnow()
    await db.commit()

    token = create_session_token(user.id)
    response.set_cookie("session_token", token, httponly=True, samesite="lax", max_age=86400 * 7)
    return UserResponse.model_validate(user)


@router.post("/invite/{token}")
async def redeem_invite_link(token: str, body: InviteSignupRequest, response: Response, db: DB):
    try:
        invite = await validate_invite_token(db, token)
        user = await redeem_invite(db, invite, name=body.name, email=body.email)
    except InviteError as e:
        raise HTTPException(status_code=400, detail=str(e))

    session_token = create_session_token(user.id)
    response.set_cookie("session_token", session_token, httponly=True, samesite="lax", max_age=86400 * 7)
    return UserResponse.model_validate(user)


@router.post("/dev-login")
async def dev_login(response: Response, db: DB):
    settings = get_settings()
    if not settings.is_local:
        raise HTTPException(status_code=404)

    result = await db.execute(select(User).where(User.email == "dev@flare.local"))
    user = result.scalar_one_or_none()
    if user is None:
        user = User(email="dev@flare.local", name="Dev Admin", role=UserRole.ADMIN)
        db.add(user)
        await db.commit()
        await db.refresh(user)

    user.last_login_at = datetime.utcnow()
    await db.commit()

    token = create_session_token(user.id)
    response.set_cookie("session_token", token, httponly=True, samesite="lax", max_age=86400 * 7)
    return UserResponse.model_validate(user)


@router.get("/me", response_model=UserResponse)
async def get_me(user: CurrentUser):
    return UserResponse.model_validate(user)


@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie("session_token")
    return {"ok": True}
