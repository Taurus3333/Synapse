"""HS256 JWT access tokens. Tenant and user are claims, not request headers."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

from synapse.auth.principal import Principal

ALGORITHM = "HS256"
DEFAULT_TTL_MINUTES = 60


def issue_access_token(
    *,
    secret: str,
    principal: Principal,
    ttl_minutes: int = DEFAULT_TTL_MINUTES,
) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": principal.user_id,
        "tid": principal.tenant_id,
        "role": principal.role,
        "email": principal.email,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=ttl_minutes)).timestamp()),
        "iss": "synapse",
    }
    return jwt.encode(payload, secret, algorithm=ALGORITHM)


def decode_access_token(token: str, secret: str) -> Principal:
    try:
        payload: dict[str, Any] = jwt.decode(
            token,
            secret,
            algorithms=[ALGORITHM],
            issuer="synapse",
            options={"require": ["exp", "sub", "tid", "role"]},
        )
    except jwt.PyJWTError as exc:
        raise ValueError("invalid or expired token") from exc
    tid = payload["tid"]
    sub = payload["sub"]
    if not isinstance(tid, str) or not tid.startswith("tnt_"):
        raise ValueError("invalid tenant claim")
    if not isinstance(sub, str) or not sub.startswith("usr_"):
        raise ValueError("invalid subject claim")
    return Principal(
        tenant_id=tid,
        user_id=sub,
        role=str(payload["role"]),
        email=str(payload.get("email") or ""),
    )
