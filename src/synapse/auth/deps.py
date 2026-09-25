"""FastAPI dependencies for authenticated principals."""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from synapse.auth.principal import Principal
from synapse.auth.tokens import decode_access_token
from synapse.platform import live as repo

_bearer = HTTPBearer(auto_error=False)


async def get_principal(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> Principal:
    if creds is None or creds.scheme.lower() != "bearer":
        raise HTTPException(401, "Bearer token required", headers={"WWW-Authenticate": "Bearer"})
    settings = request.app.state.settings
    secret = settings.jwt_secret.get_secret_value()
    try:
        principal = decode_access_token(creds.credentials, secret)
    except ValueError as exc:
        raise HTTPException(401, str(exc), headers={"WWW-Authenticate": "Bearer"}) from exc

    spoof = request.headers.get("x-tenant-id")
    if spoof and spoof != principal.tenant_id:
        raise HTTPException(403, "X-Tenant-ID does not match authenticated tenant")
    return principal


async def require_project_access(
    request: Request,
    principal: Principal = Depends(get_principal),
) -> Principal:
    key = request.path_params.get("key")
    if not key or not isinstance(key, str):
        raise HTTPException(400, "project key path required")
    async with request.app.state.sessions() as session:
        project = await repo.get_project(session, principal.tenant_id, key.upper())
        if project is None:
            # Same status for missing and cross-tenant — do not leak existence.
            raise HTTPException(404, "project not found")
        if principal.is_tenant_admin:
            return principal
        ok = await repo.user_on_project(session, principal.tenant_id, project.id, principal.user_id)
        if not ok:
            raise HTTPException(403, "not a member of this project")
    return principal


def require_writer(principal: Principal = Depends(get_principal)) -> Principal:
    if not principal.can_write:
        raise HTTPException(403, "role cannot mutate live data")
    return principal
