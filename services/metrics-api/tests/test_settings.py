"""Unit tests: the reference time the API pins for the views (ADR-0019)."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from btp_metrics_api.settings import ApiSettings, fixture_trip_ids, reference_time


def test_live_mode_uses_database_now(monkeypatch: pytest.MonkeyPatch) -> None:
    """Live mode with BTP_AS_OF empty (as compose passes it): no pinned time."""
    monkeypatch.setenv("BTP_AS_OF", "")
    assert reference_time(ApiSettings(feed_mode="live")) is None


def test_fixtures_mode_uses_recording_time() -> None:
    """Fixtures mode: the header time of the last snapshot of the replayed set."""
    pinned = reference_time(ApiSettings(feed_mode="fixtures", fixture_set="rush-hour"))
    assert pinned == datetime(2026, 11, 18, 17, 4, 51, tzinfo=UTC)
    evening = reference_time(ApiSettings(feed_mode="fixtures", fixture_set="evening"))
    assert evening == datetime(2026, 11, 18, 20, 6, 14, tzinfo=UTC)


def test_as_of_without_time_zone_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """A naive BTP_AS_OF would be read in the database's time zone: refuse it."""
    monkeypatch.setenv("BTP_AS_OF", "2026-11-18T18:04:51")
    with pytest.raises(ValidationError):
        ApiSettings()


def test_explicit_as_of_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    """BTP_AS_OF overrides the fixture time."""
    monkeypatch.setenv("BTP_AS_OF", "2026-11-18T08:00:00Z")
    pinned = reference_time(ApiSettings(feed_mode="fixtures"))
    assert pinned == datetime(2026, 11, 18, 8, 0, tzinfo=UTC)


def test_fixture_trip_ids_of_committed_set() -> None:
    """The committed rush-hour set has 172 distinct trips (fixtures/vbb-rt/rush-hour)."""
    ids = fixture_trip_ids("rush-hour")
    assert len(ids) == 172
    assert all(ids)
