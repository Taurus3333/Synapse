from __future__ import annotations

import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Literal, Protocol

import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from synapse import __version__
from synapse.api.ask_routes import router as ask_router
from synapse.api.connector_routes import router as connector_router
from synapse.api.live_routes import router as live_router
from synapse.api.memory_routes import router as memory_router
from synapse.api.obs_routes import router as obs_router
from synapse.api.run_routes import router as run_router
from synapse.auth.routes import router as auth_router
from synapse.obs.metrics import get_metrics
from synapse.platform.config import Settings, get_settings
from synapse.platform.db import Database, health_error_name
from synapse.platform.logging import (
    CORRELATION_HEADER,
    bind_correlation_id,
    configure_logging,
    get_logger,
)
from synapse.platform.redis import RedisClient
from synapse.platform.seed import session_factory

logger = get_logger(__name__)


class CheckResult(BaseModel):
    status: Literal["ok", "error"]
    latency_ms: float | None = None
    error: str | None = None


class HealthResponse(BaseModel):
    status: Literal["ok", "unhealthy"]
    version: str
    checks: dict[str, CheckResult]


class _Pinger(Protocol):
    async def ping(self) -> float: ...


async def _run_check(name: str, store: _Pinger) -> CheckResult:
    try:
        latency_ms = await store.ping()
    except Exception as exc:
        logger.warning("health_check_failed", store=name, error=health_error_name(exc))
        return CheckResult(status="error", error=health_error_name(exc))
    return CheckResult(status="ok", latency_ms=round(float(latency_ms), 3))


def _route_label(request: Request) -> str:
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    return str(path or request.url.path)


def create_app(
    settings: Settings | None = None,
    *,
    connect_stores: bool = True,
) -> FastAPI:
    resolved = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging(json_logs=resolved.log_json, level=resolved.log_level)
        app.state.settings = resolved
        if connect_stores:
            app.state.db = Database(resolved.database_dsn())
            app.state.redis = RedisClient(resolved.redis_dsn())
            await app.state.db.connect()
            await app.state.redis.connect()
            app.state.sessions = session_factory(app.state.db.engine)
        logger.info("api_started", env=resolved.env, version=__version__)
        try:
            yield
        finally:
            if connect_stores:
                await app.state.db.close()
                await app.state.redis.close()
            logger.info("api_stopped")

    app = FastAPI(title="Synapse", version=__version__, lifespan=lifespan)
    app.state.settings = resolved
    app.include_router(auth_router)
    app.include_router(live_router)
    app.include_router(ask_router)
    app.include_router(run_router)
    app.include_router(connector_router)
    app.include_router(obs_router)
    app.include_router(memory_router)

    @app.middleware("http")
    async def observability_middleware(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        correlation_id = bind_correlation_id(request.headers.get(CORRELATION_HEADER))
        started = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
        except Exception:
            duration_ms = (time.perf_counter() - started) * 1000.0
            route = _route_label(request)
            get_metrics().incr(
                "http_requests_total",
                method=request.method,
                route=route,
                status_class="5xx",
            )
            get_metrics().observe(
                "http_request_duration_ms",
                duration_ms,
                method=request.method,
                route=route,
                status_class="5xx",
            )
            logger.exception(
                "http_request_error",
                method=request.method,
                path=request.url.path,
                duration_ms=round(duration_ms, 3),
            )
            raise

        duration_ms = (time.perf_counter() - started) * 1000.0
        route = _route_label(request)
        status_class = f"{status_code // 100}xx"
        get_metrics().incr(
            "http_requests_total",
            method=request.method,
            route=route,
            status_class=status_class,
        )
        get_metrics().observe(
            "http_request_duration_ms",
            duration_ms,
            method=request.method,
            route=route,
            status_class=status_class,
        )
        logger.info(
            "http_request",
            method=request.method,
            route=route,
            status_code=status_code,
            duration_ms=round(duration_ms, 3),
        )
        response.headers[CORRELATION_HEADER] = correlation_id
        response.headers["X-Request-Duration-Ms"] = f"{duration_ms:.3f}"
        return response

    @app.get("/health")
    async def health(request: Request) -> JSONResponse:
        checks = {
            "postgres": await _run_check("postgres", request.app.state.db),
            "redis": await _run_check("redis", request.app.state.redis),
        }
        status: Literal["ok", "unhealthy"] = (
            "ok" if all(c.status == "ok" for c in checks.values()) else "unhealthy"
        )
        body = HealthResponse(status=status, version=__version__, checks=checks)
        return JSONResponse(
            status_code=200 if status == "ok" else 503,
            content=body.model_dump(),
        )

    return app


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "synapse.api.app:create_app",
        factory=True,
        host=settings.api_host,
        port=settings.api_port,
        reload=settings.env == "local",
    )
