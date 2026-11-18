"""Validation rules a version must pass before it can be activated."""

from datetime import date

from btp_schedule.importer import validation_errors

GOOD_COUNTS = {"routes": 1, "trips": 1, "stops": 1, "stop_times": 1}
RANGE = (date(2026, 11, 18), date(2027, 12, 11))


def test_valid_version_has_no_errors() -> None:
    """Non-empty tables, known trips and a range covering today pass."""
    assert validation_errors(GOOD_COUNTS, [], RANGE, date(2026, 11, 18)) == []
    assert validation_errors(GOOD_COUNTS, [], RANGE, date(2027, 12, 11)) == []


def test_empty_tables() -> None:
    """Each required table without rows is reported."""
    errors = validation_errors({"routes": 3, "stops": 0}, [], RANGE, date(2026, 12, 1))
    assert errors == ["trips has no rows", "stops has no rows", "stop_times has no rows"]


def test_unknown_trips() -> None:
    """stop_times pointing at trips that don't exist are reported with examples."""
    errors = validation_errors(GOOD_COUNTS, ["t1", "t2"], RANGE, date(2026, 12, 1))
    assert errors == ["stop_times reference unknown trips, e.g. t1, t2"]


def test_date_range_must_cover_today() -> None:
    """A feed that ended yesterday or starts tomorrow is not usable today."""
    assert validation_errors(GOOD_COUNTS, [], RANGE, date(2026, 11, 17)) == [
        "feed dates 2026-11-18..2027-12-11 don't cover 2026-11-17"
    ]
    assert validation_errors(GOOD_COUNTS, [], RANGE, date(2027, 12, 12))
    assert validation_errors(GOOD_COUNTS, [], (None, None), date(2026, 12, 1)) == [
        "no feed date range (feed_info, calendar and calendar_dates are empty)"
    ]
