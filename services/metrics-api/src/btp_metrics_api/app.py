"""FastAPI app: GET /health and GET /v1/now/by-mode over the marts views."""

import logging
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated, Any

import psycopg
from fastapi import Depends, FastAPI, Request, Response
from fastapi.responses import JSONResponse
from psycopg_pool import ConnectionPool

from btp_metrics_api import docs, queries
from btp_metrics_api.models import (
    Health,
    NowByModeResponse,
    check_health,
    database_unavailable,
    shape_now_by_mode,
)
from btp_metrics_api.settings import ApiSettings, reference_time

log = logging.getLogger(__name__)

POOL_TIMEOUT_S = 5.0
CACHE_CONTROL = "public, max-age=30"
Connection = psycopg.Connection[Any]


def make_pool(settings: ApiSettings, as_of: datetime | None) -> ConnectionPool[Connection]:
    """Create (not open) a small pool of autocommit connections as btp_api."""

    def configure(conn: Connection) -> None:
        """Pin the reference time on every new connection when one is set."""
        if as_of is not None:
            queries.set_as_of(conn, as_of)

    return ConnectionPool(
        kwargs={
            "host": settings.pg_host,
            "port": settings.pg_port,
            "dbname": settings.pg_db,
            "user": settings.pg_user,
            "password": settings.pg_api_password,
            "autocommit": True,
            "connect_timeout": 5,
        },
        min_size=1,
        max_size=settings.pool_max_size,
        configure=configure,
        check=ConnectionPool.check_connection,
        open=False,
    )


def create_app(settings: ApiSettings) -> FastAPI:
    """Build the app; the pool opens in the lifespan, so tests can skip it."""
    as_of = reference_time(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """Open the pool without waiting, so /health can report a database that is down."""
        pool = make_pool(settings, as_of)
        pool.open(wait=False)
        app.state.pool = pool
        log.info("started", extra={"fields": {"as_of": as_of, "feed_mode": settings.feed_mode}})
        try:
            yield
        finally:
            pool.close()

    app = FastAPI(
        title="Berlin Transit Pulse metrics API",
        description="Read-only. Data: VBB Verkehrsverbund Berlin-Brandenburg GmbH, CC-BY 4.0.",
        version="0.1.0",
        lifespan=lifespan,
        # No trailing-slash redirects: behind nginx and CloudFront the app can't build a correct
        # absolute URL (it sees the origin's host, plain HTTP and no /api prefix), so a
        # redirect would send viewers to an unreachable address. /v1/now/by-mode/ is a 404.
        redirect_slashes=False,
        # FastAPI's own pages load from a CDN and run inline scripts, which the site's CSP
        # blocks. /docs is self-hosted instead (docs.py); ReDoc is dropped.
        docs_url=None,
        redoc_url=None,
    )
    app.include_router(docs.router)
    app.mount("/docs-assets", docs.assets(), name="docs-assets")
    app.state.settings = settings
    app.state.as_of = as_of

    @app.exception_handler(psycopg.Error)
    async def _db_error(request: Request, exc: psycopg.Error) -> JSONResponse:
        """Answer 503 when the database is unreachable or a query fails."""
        log.warning("database error", extra={"fields": {"error": str(exc).strip()}})
        return JSONResponse(status_code=503, content={"detail": "database unavailable"})

    @app.get("/health", response_model=Health, responses={503: {"model": Health}})
    def health(request: Request, response: Response) -> Health:
        """Return 200 if the database answers and the feed data is fresh, else 503."""
        try:
            with request.app.state.pool.connection(timeout=POOL_TIMEOUT_S) as conn:
                latest = queries.latest_feed_ts(conn)
        except psycopg.Error as exc:
            result = database_unavailable(exc)
        else:
            result = check_health(latest, datetime.now(UTC), settings.feed_max_age_s)
        if result.status != "ok":
            response.status_code = 503
        return result

    @app.get("/v1/now/by-mode", response_model=NowByModeResponse)
    def now_by_mode(
        conn: Annotated[Connection, Depends(get_connection)], response: Response
    ) -> NowByModeResponse:
        """Return Berlin trips running now and their current delay, per mode."""
        response.headers["Cache-Control"] = CACHE_CONTROL
        return shape_now_by_mode(queries.now_by_mode(conn), as_of, datetime.now(UTC))

    return app


def get_connection(request: Request) -> Iterator[Connection]:
    """Lend a pooled connection for one request."""
    with request.app.state.pool.connection(timeout=POOL_TIMEOUT_S) as conn:
        yield conn
