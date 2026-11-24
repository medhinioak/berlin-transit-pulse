"""Unit tests: response shaping, health logic and attribution."""

from datetime import UTC, datetime, timedelta
from typing import Any

from btp_metrics_api.models import ATTRIBUTION, check_health, schedule_mismatch, shape_now_by_mode

NOW = datetime(2026, 11, 19, 10, 0, tzinfo=UTC)
COMPUTED = datetime(2026, 11, 19, 9, 59, 58, tzinfo=UTC)


def row(mode: str, **overrides: Any) -> dict[str, Any]:
    """Return a view row with plausible values."""
    base: dict[str, Any] = {
        "mode": mode,
        "running_trips": 22,
        "trips_with_nonzero_delay": 11,
        "avg_delay_s": 222.9,
        "p50_delay_s": 0.0,
        "p95_delay_s": 1440.0,
        "cancelled_trips_today": 1,
        "latest_feed_ts": NOW - timedelta(seconds=20),
        "computed_at": COMPUTED,
    }
    return base | overrides


def test_response_is_labelled_live_indicator_with_attribution() -> None:
    """Every data response says what it is and credits VBB under CC-BY 4.0."""
    body = shape_now_by_mode([row("bus")], None, NOW).model_dump(mode="json")
    assert body["kind"] == "live-indicator"
    assert body["attribution"] == ATTRIBUTION
    assert "CC-BY 4.0" in body["attribution"]
    assert "VBB" in body["attribution"]


def test_empty_response_still_carries_attribution() -> None:
    """No running trips (e.g. at night) is an empty list, still attributed."""
    body = shape_now_by_mode([], None, NOW).model_dump(mode="json")
    assert body["modes"] == []
    assert body["attribution"] == ATTRIBUTION
    assert body["as_of"] == "2026-11-19T10:00:00Z"


def test_as_of_is_the_pinned_reference_time_when_set() -> None:
    """Fixtures mode: as_of is the recording time, not the database clock."""
    pinned = datetime(2026, 11, 18, 17, 4, 51, tzinfo=UTC)
    assert shape_now_by_mode([row("bus")], pinned, NOW).as_of == pinned


def test_as_of_is_the_database_time_in_live_mode() -> None:
    """Live mode: as_of is when the database computed the rows."""
    assert shape_now_by_mode([row("bus")], None, NOW).as_of == COMPUTED


def test_rows_keep_view_columns_and_null_delays() -> None:
    """A mode with only cancellations has no delays: null, not 0."""
    rows = [
        row("bus"),
        row(
            "ferry",
            running_trips=0,
            trips_with_nonzero_delay=0,
            avg_delay_s=None,
            p50_delay_s=None,
            p95_delay_s=None,
        ),
    ]
    modes = shape_now_by_mode(rows, None, NOW).model_dump(mode="json")["modes"]
    assert [m["mode"] for m in modes] == ["bus", "ferry"]
    assert modes[0]["p95_delay_s"] == 1440.0
    assert modes[1]["avg_delay_s"] is None
    assert modes[1]["running_trips"] == 0
    assert set(modes[0]) == set(row("bus"))


def test_health_ok_with_fresh_feed() -> None:
    """Feed data 20 s old is healthy."""
    health = check_health(NOW - timedelta(seconds=20), NOW, 300)
    assert health.status == "ok"
    assert health.feed_age_s == 20


def test_health_unavailable_with_stale_feed() -> None:
    """Feed data older than the limit is unhealthy, with the age in the reason."""
    health = check_health(NOW - timedelta(minutes=6), NOW, 300)
    assert health.status == "unavailable"
    assert health.reason == "feed data is 360 s old (limit 300 s)"


def test_health_unavailable_exactly_at_the_limit() -> None:
    """The limit is exclusive: 'less than 5 min old'."""
    assert check_health(NOW - timedelta(seconds=300), NOW, 300).status == "unavailable"


def test_health_tolerates_small_clock_skew() -> None:
    """A feed header a few seconds ahead of our clock is fine."""
    assert check_health(NOW + timedelta(seconds=10), NOW, 300).status == "ok"


def test_health_unavailable_with_future_feed_time() -> None:
    """A feed time far in the future (bad header, leftover test rows) is not fresh data."""
    health = check_health(NOW + timedelta(days=1), NOW, 300)
    assert health.status == "unavailable"
    assert health.reason == "feed data is 86400 s in the future"


def test_health_unavailable_without_data() -> None:
    """An empty events table (before the first message) is unhealthy."""
    health = check_health(None, NOW, 300)
    assert health.status == "unavailable"
    assert health.reason == "no feed data in the database"


def test_schedule_matches_fixtures() -> None:
    """All fixture trips in the active version: no problem."""
    assert schedule_mismatch("abc123", 172, 172, "rush-hour") is None


def test_schedule_missing_trips_names_version_and_fix() -> None:
    """Another version active (#75: a republished live GTFS): the message says what to do."""
    reason = schedule_mismatch("8255349c4cd3", 16, 172, "rush-hour")
    assert reason is not None
    assert "active schedule version 8255349c4cd3 has 16 of the 172 trips" in reason
    assert "'rush-hour'" in reason
    assert "make down && make up" in reason
    assert "make fixtures" in reason
    assert "btp-schedule activate" in reason


def test_schedule_without_active_version() -> None:
    """No active version at all is a problem too, not an empty but healthy answer."""
    reason = schedule_mismatch(None, 0, 172, "evening")
    assert reason is not None
    assert reason.startswith("no active schedule has 0 of the 172 trips")
