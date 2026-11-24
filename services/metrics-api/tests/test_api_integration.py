"""Integration tests against the compose stack: the API serves fixture data, read-only.

Needs `make up` in fixtures mode (the default). Override the URL with BTP_API_URL and the
passwords with BTP_PG_API_PASSWORD and BTP_PG_SUPERUSER_PASSWORD.
"""

import os
import time
from datetime import UTC, datetime
from typing import Any

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import errors

from btp_metrics_api import app as app_module
from btp_metrics_api import queries
from btp_metrics_api.models import schedule_mismatch
from btp_metrics_api.settings import ApiSettings, fixture_as_of, fixture_trip_ids

pytestmark = pytest.mark.integration

API = os.environ.get("BTP_API_URL", "http://localhost:8000")
FIXTURE_SET = "rush-hour"  # compose's default BTP_FIXTURE_SET
PG_HOST = os.environ.get("BTP_PG_HOST", "localhost")
PG_PORT = int(os.environ.get("BTP_PG_PORT", "5432"))


def connect(user: str, password: str) -> psycopg.Connection[Any]:
    """Connect to the stack's database in autocommit mode."""
    return psycopg.connect(
        host=PG_HOST,
        port=PG_PORT,
        dbname="btp",
        user=user,
        password=password,
        autocommit=True,
        connect_timeout=5,
    )


def api_conn() -> psycopg.Connection[Any]:
    """Connect as the API's read-only role."""
    return connect("btp_api", os.environ.get("BTP_PG_API_PASSWORD", "dev_api"))


def schedule_problem(conn: psycopg.Connection[Any]) -> str | None:
    """Run the API's fixtures-mode check: are the replayed trips in the active schedule?"""
    trip_ids = fixture_trip_ids(FIXTURE_SET)
    version_id, found = queries.schedule_coverage(conn, trip_ids)
    return schedule_mismatch(version_id, found, len(trip_ids), FIXTURE_SET)


@pytest.fixture(scope="module")
def schedule_matches_fixtures() -> None:
    """Fail in seconds with the fix, instead of waiting 60 s for rows that can't appear."""
    with api_conn() as conn:
        problem = schedule_problem(conn)
    if problem:
        pytest.fail(problem, pytrace=False)


def wait_for_modes(modes: set[str], timeout_s: float = 60) -> dict[str, Any]:
    """Poll /v1/now/by-mode until the given modes have running trips."""
    deadline = time.monotonic() + timeout_s
    while True:
        body: dict[str, Any] = httpx.get(f"{API}/v1/now/by-mode", timeout=10).json()
        running = {m["mode"] for m in body["modes"] if m["running_trips"] > 0}
        if modes <= running or time.monotonic() > deadline:
            return body
        time.sleep(2)


@pytest.mark.usefixtures("schedule_matches_fixtures")
def test_now_by_mode_serves_fixture_data() -> None:
    """Bus and tram rows with plausible values, labelled and attributed."""
    body = wait_for_modes({"bus", "tram"})
    assert body["kind"] == "live-indicator"
    assert body["attribution"] == "Data: VBB Verkehrsverbund Berlin-Brandenburg GmbH, CC-BY 4.0"
    modes = {m["mode"]: m for m in body["modes"]}
    assert {"bus", "tram"} <= set(modes), body
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


@pytest.mark.usefixtures("schedule_matches_fixtures")
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


def test_wrong_active_schedule_fails_clearly() -> None:
    """Another version active (#75): the check names it and the fix, not an empty answer.

    Runs as the superuser in a transaction that is rolled back, so the stack is untouched.
    """
    with connect("postgres", os.environ.get("BTP_PG_SUPERUSER_PASSWORD", "dev_superuser")) as conn:
        with conn.transaction(force_rollback=True):
            conn.execute("UPDATE schedule.versions SET status = 'retired' WHERE status = 'active'")
            conn.execute(
                "INSERT INTO schedule.versions (version_id, sha256, source_url, status) "
                "VALUES ('test-wrong', 'test', 'test:wrong', 'active')"
            )
            conn.execute(
                "SELECT set_config('btp.as_of', %s, true)", [fixture_as_of(FIXTURE_SET).isoformat()]
            )
            # Without the check, this is all a client would see:
            assert conn.execute("SELECT count(*) FROM marts.v_now_by_mode").fetchone() == (0,)
            problem = schedule_problem(conn)
    assert problem is not None
    expected = len(fixture_trip_ids(FIXTURE_SET))
    assert problem.startswith(f"active schedule version test-wrong has 0 of the {expected} trips")
    assert "make down && make up" in problem


@pytest.mark.usefixtures("schedule_matches_fixtures")
def test_fixture_data_served_after_timetable_change(monkeypatch: pytest.MonkeyPatch) -> None:
    """With the clock after 2026-12-13 and BTP_AS_OF unset, fixture data is still served.

    Runs the app in-process against the stack's database, with the app's clock faked to
    2026-12-20: the Python side derives "now" from the recording, not the clock. The database
    clock can't be faked, but it is already days past the recording (2026-11-18), outside the
    views' today/yesterday window, so a view reading now() instead of btp.as_of (ADR-0019)
    fails here today. 13 Dec matters only for the schedule, which is the mini GTFS: the
    schedule tests import it with today after the change.
    """

    class After(datetime):
        """datetime whose now() is a week after the timetable change."""

        @classmethod
        def now(cls, tz: Any = None) -> "After":
            """Return 2026-12-20 12:00 UTC."""
            return cls(2026, 12, 20, 12, 0, tzinfo=UTC)

    wait_for_modes({"bus", "tram"})  # the replay has reached the database
    monkeypatch.setattr(app_module, "datetime", After)
    settings = ApiSettings(
        feed_mode="fixtures", fixture_set=FIXTURE_SET, as_of=None, pg_host=PG_HOST, pg_port=PG_PORT
    )
    with TestClient(app_module.create_app(settings)) as client:
        body = client.get("/v1/now/by-mode").json()
    running = {m["mode"] for m in body["modes"] if m["running_trips"] > 0}
    assert {"bus", "tram"} <= running, body
    assert datetime.fromisoformat(body["as_of"]) == fixture_as_of(FIXTURE_SET)
