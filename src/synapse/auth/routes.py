"""Auth HTTP routes: token exchange and identity."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from synapse.auth.deps import get_principal
from synapse.auth.passwords import verify_password
from synapse.auth.principal import Principal
from synapse.auth.tokens import issue_access_token
from synapse.platform.schema import AuthCredentialRow, UserRow

router = APIRouter(prefix="/v1/auth", tags=["auth"])


class TokenRequest(BaseModel):
    email: str = Field(min_length=3, max_length=256)
    password: str = Field(min_length=8, max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    tenant_id: str
    user_id: str
    role: str


@router.post("/token", response_model=TokenResponse)
async def token(body: TokenRequest, request: Request) -> TokenResponse:
    settings = request.app.state.settings
    async with request.app.state.sessions() as session:
        result = await session.execute(select(UserRow).where(UserRow.email == str(body.email).lower()))
        user = result.scalar_one_or_none()
        if user is None or not user.is_active:
            raise HTTPException(401, "invalid credentials")
        cred = await session.get(AuthCredentialRow, user.id)
        if cred is None or not verify_password(body.password, cred.password_hash):
            raise HTTPException(401, "invalid credentials")

    principal = Principal(
        tenant_id=user.tenant_id,
        user_id=user.id,
        role=user.role,
        email=user.email,
    )
    ttl = settings.jwt_ttl_minutes
    access = issue_access_token(
        secret=settings.jwt_secret.get_secret_value(),
        principal=principal,
        ttl_minutes=ttl,
    )
    return TokenResponse(
        access_token=access,
        expires_in=ttl * 60,
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        role=principal.role,
    )


@router.get("/me")
async def me(principal: Principal = Depends(get_principal)) -> dict:
    return {
        "user_id": principal.user_id,
        "tenant_id": principal.tenant_id,
        "role": principal.role,
        "email": principal.email,
        "can_write": principal.can_write,
    }
