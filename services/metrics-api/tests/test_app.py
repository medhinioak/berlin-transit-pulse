"""Unit tests for the HTTP layer, with the database replaced by stubs."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import psycopg
import pytest
from fastapi.testclient import TestClient

from btp_metrics_api import app as app_module
from btp_metrics_api import queries
from btp_metrics_api.models import ATTRIBUTION
from btp_metrics_api.settings import ApiSettings, fixture_trip_ids


class FakePool:
    """Stands in for the psycopg pool; raises the given error instead of connecting."""

    def __init__(self, error: Exception | None = None) -> None:
        """Remember the error to raise, if any."""
        self.error = error

    @contextmanager
    def connection(self, timeout: float | None = None) -> Iterator[object]:
        """Yield a dummy connection or raise."""
        if self.error:
            raise self.error
        yield object()


def client(
    pool: FakePool,
    monkeypatch: pytest.MonkeyPatch,
    feed_mode: Literal["live", "fixtures"] = "live",
    **queries_stub: Any,
) -> TestClient:
    """Build the app without its lifespan (no real pool) and stub the queries."""
    for name, value in queries_stub.items():
        monkeypatch.setattr(queries, name, value)
    app = app_module.create_app(ApiSettings(feed_mode=feed_mode))
    app.state.pool = pool
    return TestClient(app)


def test_now_by_mode_response(monkeypatch: pytest.MonkeyPatch) -> None:
    """The endpoint returns the view rows, the label, attribution and a 30 s cache header."""
    now = datetime.now(UTC)
    rows = [
        {
            "mode": "tram",
            "running_trips": 19,
            "trips_with_nonzero_delay": 15,
            "avg_delay_s": 375.8,
            "p50_delay_s": 120.0,
            "p95_delay_s": 1620.0,
            "cancelled_trips_today": 0,
            "latest_feed_ts": now,
            "computed_at": now,
        }
    ]
    c = client(FakePool(), monkeypatch, now_by_mode=lambda conn: rows)
    response = c.get("/v1/now/by-mode")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "public, max-age=30"
    body = response.json()
    assert body["kind"] == "live-indicator"
    assert body["attribution"] == ATTRIBUTION
    assert body["modes"][0]["mode"] == "tram"


def test_now_by_mode_database_down(monkeypatch: pytest.MonkeyPatch) -> None:
    """A database that can't be reached gives 503, not 500."""
    c = client(FakePool(psycopg.OperationalError("down")), monkeypatch)
    response = c.get("/v1/now/by-mode")
    assert response.status_code == 503
    assert response.json() == {"detail": "database unavailable"}


def test_health_fresh(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fresh feed data: 200."""
    fresh = datetime.now(UTC) - timedelta(seconds=10)
    c = client(FakePool(), monkeypatch, latest_feed_ts=lambda conn: fresh)
    response = c.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_health_stale_feed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Feed data older than 5 min: 503 with the reason."""
    stale = datetime.now(UTC) - timedelta(minutes=10)
    c = client(FakePool(), monkeypatch, latest_feed_ts=lambda conn: stale)
    response = c.get("/health")
    assert response.status_code == 503
    assert response.json()["reason"].startswith("feed data is 6")


def test_health_database_down(monkeypatch: pytest.MonkeyPatch) -> None:
    """Database unreachable: 503 naming the database."""
    c = client(FakePool(psycopg.OperationalError("down")), monkeypatch)
    response = c.get("/health")
    assert response.status_code == 503
    assert response.json()["reason"] == "database unavailable: OperationalError"


def test_trailing_slash_is_not_redirected(monkeypatch: pytest.MonkeyPatch) -> None:
    """A trailing slash is a 404, not a redirect to an absolute URL the viewer can't reach."""
    c = client(FakePool(), monkeypatch, now_by_mode=lambda conn: [])
    response = c.get("/v1/now/by-mode/", follow_redirects=False)
    assert response.status_code == 404
    assert "location" not in response.headers


def test_docs_page_is_self_hosted(monkeypatch: pytest.MonkeyPatch) -> None:
    """/docs links only relative, same-origin assets and has no inline script (site CSP)."""
    c = client(FakePool(), monkeypatch)
    html = c.get("/docs").text
    assert 'src="docs-assets/init.js"' in html
    assert "<script>" not in html
    assert "http" not in html
    assert c.get("/docs-assets/init.js").status_code == 200
    assert c.get("/redoc").status_code == 404
    assert c.get("/openapi.json").status_code == 200


def test_health_fixtures_schedule_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fixtures mode with the wrong schedule active: 503 naming the version, despite fresh data."""
    fresh = datetime.now(UTC)
    c = client(
        FakePool(),
        monkeypatch,
        feed_mode="fixtures",
        latest_feed_ts=lambda conn: fresh,
        schedule_coverage=lambda conn, ids: ("8255349c4cd3", 16),
    )
    response = c.get("/health")
    assert response.status_code == 503
    reason = response.json()["reason"]
    expected = len(fixture_trip_ids("rush-hour"))
    assert reason.startswith(f"active schedule version 8255349c4cd3 has 16 of the {expected} trips")


def test_health_fixtures_schedule_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fixtures mode with every replayed trip in the active schedule: 200."""
    fresh = datetime.now(UTC)
    c = client(
        FakePool(),
        monkeypatch,
        feed_mode="fixtures",
        latest_feed_ts=lambda conn: fresh,
        schedule_coverage=lambda conn, ids: ("mini", len(ids)),
    )
    assert c.get("/health").status_code == 200


def test_health_live_mode_skips_schedule_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """Live mode doesn't compare the schedule with the fixtures."""

    def fail(conn: object, ids: object) -> None:
        raise AssertionError("schedule_coverage called in live mode")

    fresh = datetime.now(UTC)
    c = client(FakePool(), monkeypatch, latest_feed_ts=lambda conn: fresh, schedule_coverage=fail)
    assert c.get("/health").status_code == 200
