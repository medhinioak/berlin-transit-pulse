"""Integration tests against the compose stack: the API serves fixture data, read-only.

Needs `make up` in fixtures mode (the default). Override the URL with BTP_API_URL and the
password with BTP_PG_API_PASSWORD.
"""

import os
import time
from typing import Any

import httpx
import psycopg
import pytest
from psycopg import errors

pytestmark = pytest.mark.integration

API = os.environ.get("BTP_API_URL", "http://localhost:8000")


def api_conn() -> psycopg.Connection[Any]:
    """Connect as the API's read-only role."""
    return psycopg.connect(
        host=os.environ.get("BTP_PG_HOST", "localhost"),
        port=int(os.environ.get("BTP_PG_PORT", "5432")),
        dbname="btp",
        user="btp_api",
        password=os.environ.get("BTP_PG_API_PASSWORD", "dev_api"),
        autocommit=True,
    )


def wait_for_modes(modes: set[str], timeout_s: float = 60) -> dict[str, Any]:
    """Poll /v1/now/by-mode until the given modes have running trips."""
    deadline = time.monotonic() + timeout_s
    while True:
        body: dict[str, Any] = httpx.get(f"{API}/v1/now/by-mode", timeout=10).json()
        running = {m["mode"] for m in body["modes"] if m["running_trips"] > 0}
        if modes <= running or time.monotonic() > deadline:
            return body
        time.sleep(2)


def test_now_by_mode_serves_fixture_data() -> None:
    """Bus and tram rows with plausible values, labelled and attributed."""
    body = wait_for_modes({"bus", "tram"})
    assert body["kind"] == "live-indicator"
    assert body["attribution"] == "Data: VBB Verkehrsverbund Berlin-Brandenburg GmbH, CC-BY 4.0"
    modes = {m["mode"]: m for m in body["modes"]}
    assert {"bus", "tram"} <= set(modes)
    for name in ("bus", "tram"):
        m = modes[name]
        assert m["running_trips"] > 0
        assert m["trips_with_nonzero_delay"] <= m["running_trips"]
        assert -600 <= m["avg_delay_s"] <= 3600
        assert m["p50_delay_s"] <= m["p95_delay_s"]


def test_cache_header() -> None:
    """Responses may be cached for 30 s."""
    response = httpx.get(f"{API}/v1/now/by-mode", timeout=10)
    assert response.headers["cache-control"] == "public, max-age=30"


def test_health_ok() -> None:
    """Fixtures replay keeps the feed time fresh: 200."""
    response = httpx.get(f"{API}/health", timeout=10)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "ok"


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1 FROM events.trip_state LIMIT 1",
        "SELECT 1 FROM events.stop_predictions LIMIT 1",
        "SELECT 1 FROM schedule.v_trips LIMIT 1",
        "CREATE VIEW marts.x AS SELECT 1",
        "CREATE TABLE public.x (a int)",
    ],
)
def test_api_role_is_read_only_on_marts(sql: str) -> None:
    """btp_api can't read events or schedule, and can't create anything."""
    with api_conn() as conn, pytest.raises(errors.InsufficientPrivilege):
        conn.execute(sql)


def test_api_role_reads_marts() -> None:
    """btp_api can read the marts views."""
    with api_conn() as conn:
        assert conn.execute("SELECT count(*) FROM marts.v_now_by_mode").fetchone()
