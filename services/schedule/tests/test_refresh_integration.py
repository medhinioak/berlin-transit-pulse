"""Integration tests: staging, activate-due, the version of each service day, pruning, refresh.

Needs `make up`; the fixtures are in conftest.py and restore every version afterwards. The swap
is simulated in 2030 (activation times passed explicitly), so it never overlaps the dev
stack's own activations.
"""

from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
import pytest

from btp_schedule.cli import main
from btp_schedule.importer import (
    ImportFailed,
    NotStageableYet,
    activate,
    apply_schema,
    import_source,
)
from btp_schedule.lifecycle import BERLIN
from btp_schedule.refresh import activate_due, prune

pytestmark = pytest.mark.integration

Conn = psycopg.Connection[Any]
ZipMaker = Callable[..., Path]
Calendar = Callable[[date, date], dict[str, str]]

SWAP = date(2030, 12, 13)  # first day of the new timetable
EVE = SWAP - timedelta(days=1)
END = date(2031, 12, 13)


def _one(conn: Conn, query: str, *params: object) -> Any:
    """Return the first column of the first row."""
    row = conn.execute(query, params).fetchone()
    assert row is not None
    return row[0]


def _status(conn: Conn, version_id: str) -> str | None:
    """Return a version's status, or None if it doesn't exist."""
    row = conn.execute(
        "SELECT status FROM schedule.versions WHERE version_id = %s", (version_id,)
    ).fetchone()
    return None if row is None else str(row[0])


def _version(
    conn: Conn,
    make_zip: ZipMaker,
    calendar_between: Calendar,
    tmp_path: Path,
    start: date,
    today: date,
    *,
    activate_after: bool = False,
) -> str:
    """Import a mini GTFS valid from start to END, as of today; return its version id."""
    path = make_zip(tmp_path / f"{start}.zip", replace=calendar_between(start, END))
    return import_source(
        conn, path, f"test:{start}", today=today, activate_after=activate_after
    ).version_id


def _at(d: date, hour: int, minute: int = 0) -> datetime:
    """Return d at hour:minute Europe/Berlin."""
    return datetime(d.year, d.month, d.day, hour, minute, tzinfo=BERLIN)


def test_future_feed_is_staged(
    conn: Conn, make_zip: ZipMaker, calendar_between: Calendar, tmp_path: Path
) -> None:
    """A feed starting within 21 days is ready with activate_on; --activate leaves it staged."""
    today = SWAP - timedelta(days=21)
    version = _version(conn, make_zip, calendar_between, tmp_path, SWAP, today, activate_after=True)
    assert _status(conn, version) == "ready"
    assert (
        _one(conn, "SELECT activate_on FROM schedule.versions WHERE version_id = %s", version)
        == SWAP
    )

    # Importing the same zip again (the next deploy) keeps it staged too.
    again = import_source(
        conn,
        make_zip(tmp_path / "again.zip", replace=calendar_between(SWAP, END)),
        "test:again",
        today=today + timedelta(days=1),
        activate_after=True,
    )
    assert not again.imported
    assert _status(conn, version) == "ready"


def test_feed_too_far_ahead_fails(
    conn: Conn, make_zip: ZipMaker, calendar_between: Calendar, tmp_path: Path
) -> None:
    """22 days ahead is refused with the reason, before loading: no version row is written."""
    versions = _one(conn, "SELECT count(*) FROM schedule.versions")
    with pytest.raises(NotStageableYet, match="can be staged from 2030-11-22"):
        _version(conn, make_zip, calendar_between, tmp_path, SWAP, SWAP - timedelta(days=22))
    assert _one(conn, "SELECT count(*) FROM schedule.versions") == versions


def test_passed_over_staged_version_never_comes_back(
    conn: Conn, make_zip: ZipMaker, calendar_between: Calendar, tmp_path: Path
) -> None:
    """Two staged, caught up late: the newer wins, and the older is not activated next night."""
    early = SWAP - timedelta(days=12)
    older = _version(conn, make_zip, calendar_between, tmp_path, early, early - timedelta(days=5))
    newer = _version(conn, make_zip, calendar_between, tmp_path, SWAP, SWAP - timedelta(days=5))
    late = SWAP + timedelta(days=1)
    assert activate_due(conn, late, at=_at(late, 23, 40)) == newer
    assert (
        _one(conn, "SELECT activate_on FROM schedule.versions WHERE version_id = %s", older) is None
    )
    nxt = late + timedelta(days=1)
    assert activate_due(conn, nxt, at=_at(nxt, 23, 40)) is None
    assert activate_due(conn, nxt, lead_days=0, at=_at(nxt, 3, 30)) is None
    assert _status(conn, newer) == "active"


def test_direct_activation_supersedes_an_overdue_staged_version(
    conn: Conn, make_zip: ZipMaker, calendar_between: Calendar, tmp_path: Path
) -> None:
    """A republish activated at once after a missed eve wins over the overdue staged version."""
    staged = _version(conn, make_zip, calendar_between, tmp_path, SWAP, SWAP - timedelta(days=5))
    later = SWAP + timedelta(days=2)
    republish = _version(
        conn, make_zip, calendar_between, tmp_path, SWAP - timedelta(days=30), later
    )
    activate(conn, republish, at=_at(later, 3, 30))  # as the refresh's import does that morning
    assert _status(conn, republish) == "active"
    # The refresh's catch-up right after must not swap the staged version in.
    assert activate_due(conn, later, lead_days=0) is None
    assert _status(conn, staged) == "ready"


def test_activate_due_on_the_eve_and_late(
    conn: Conn, make_zip: ZipMaker, calendar_between: Calendar, tmp_path: Path
) -> None:
    """Nothing two days before; the eve activates; a late run catches up from 00:00."""
    old = _version(
        conn,
        make_zip,
        calendar_between,
        tmp_path,
        SWAP - timedelta(days=60),
        EVE - timedelta(days=30),
    )
    activate(conn, old, at=_at(EVE - timedelta(days=30), 12))
    new = _version(conn, make_zip, calendar_between, tmp_path, SWAP, SWAP - timedelta(days=10))

    assert (
        activate_due(conn, SWAP - timedelta(days=2), at=_at(SWAP - timedelta(days=2), 23, 40))
        is None
    )
    assert _status(conn, new) == "ready"
    assert activate_due(conn, EVE, lead_days=0, at=_at(EVE, 3, 30)) is None  # morning refresh waits

    assert activate_due(conn, EVE, at=_at(EVE, 23, 40)) == new
    assert _status(conn, new) == "active"
    assert _status(conn, old) == "retired"
    effective = _one(
        conn,
        "SELECT effective_from FROM schedule.version_activations "
        "WHERE version_id = %s AND deactivated_at IS NULL",
        new,
    )
    assert effective == _at(EVE, 23, 40)


def test_late_activation_counts_from_the_start_day(
    conn: Conn, make_zip: ZipMaker, calendar_between: Calendar, tmp_path: Path
) -> None:
    """activate-due missed the eve: the catch-up on the 14th still applies from the 13th."""
    old = _version(
        conn,
        make_zip,
        calendar_between,
        tmp_path,
        SWAP - timedelta(days=60),
        EVE - timedelta(days=30),
    )
    activate(conn, old, at=_at(EVE - timedelta(days=30), 12))
    new = _version(conn, make_zip, calendar_between, tmp_path, SWAP, SWAP - timedelta(days=10))

    assert (
        activate_due(conn, SWAP + timedelta(days=1), at=_at(SWAP + timedelta(days=1), 23, 40))
        == new
    )
    assert _one(conn, "SELECT schedule.version_for_service_date(%s)", SWAP) == new
    assert _one(conn, "SELECT schedule.version_for_service_date(%s)", EVE) == old


def test_version_for_service_date_across_the_swap(
    conn: Conn, make_zip: ZipMaker, calendar_between: Calendar, tmp_path: Path
) -> None:
    """The 12th is old, the 13th new; a trip of the 12th running at 00:30 on the 13th stays old."""
    old = _version(
        conn,
        make_zip,
        calendar_between,
        tmp_path,
        SWAP - timedelta(days=60),
        EVE - timedelta(days=30),
    )
    activate(conn, old, at=_at(EVE - timedelta(days=30), 12))
    new = _version(conn, make_zip, calendar_between, tmp_path, SWAP, SWAP - timedelta(days=10))
    activate_due(conn, EVE, at=_at(EVE, 23, 40))

    assert _one(conn, "SELECT schedule.version_for_service_date(%s)", EVE) == old
    assert _one(conn, "SELECT schedule.version_for_service_date(%s)", SWAP) == new
    assert (
        _one(conn, "SELECT schedule.version_for_service_date(%s)", SWAP + timedelta(days=30)) == new
    )

    # Any trip: its row for the 12th comes from the old version, for the 13th from the new.
    trip = _one(conn, "SELECT trip_id FROM schedule.trips WHERE version_id = %s LIMIT 1", old)
    for service_date, expected in ((EVE, old), (SWAP, new)):
        rows = conn.execute(
            "SELECT version_id FROM schedule.v_trips_by_service_date "
            "WHERE %s BETWEEN first_service_date AND last_service_date AND trip_id = %s",
            (service_date, trip),
        ).fetchall()
        assert rows == [(expected,)]
        routes = _one(
            conn,
            "SELECT count(DISTINCT version_id) FROM schedule.v_routes_by_service_date "
            "WHERE %s BETWEEN first_service_date AND last_service_date",
            service_date,
        )
        assert routes == 1
        stop_version = _one(
            conn,
            "SELECT DISTINCT version_id FROM schedule.v_stop_times_by_service_date "
            "WHERE %s BETWEEN first_service_date AND last_service_date AND trip_id = %s",
            service_date,
            trip,
        )
        assert stop_version == expected
    # "Now" views show the active version only.
    assert _one(conn, "SELECT version_id FROM schedule.v_active_version") == new


def test_dates_before_the_first_activation_get_the_earliest_version(conn: Conn) -> None:
    """Fixtures replayed from before the stack started still find a version."""
    first = _one(
        conn,
        "SELECT version_id FROM schedule.version_activations ORDER BY effective_from LIMIT 1",
    )
    assert _one(conn, "SELECT schedule.version_for_service_date(%s)", date(2000, 1, 1)) == first


def test_prune_keeps_active_previous_and_staged(
    conn: Conn, make_zip: ZipMaker, calendar_between: Calendar, tmp_path: Path
) -> None:
    """Older retired and old failed versions are deleted with all their rows."""
    others = _one(conn, "SELECT count(*) FROM schedule.versions WHERE status <> 'active'")
    if others:
        pytest.skip("the dev stack has non-active versions that prune would delete")
    stack = _one(conn, "SELECT version_id FROM schedule.v_active_version")

    today = SWAP - timedelta(days=10)
    oldest = _version(conn, make_zip, calendar_between, tmp_path, today - timedelta(days=90), today)
    older = _version(conn, make_zip, calendar_between, tmp_path, today - timedelta(days=60), today)
    current = _version(
        conn, make_zip, calendar_between, tmp_path, today - timedelta(days=30), today
    )
    staged = _version(conn, make_zip, calendar_between, tmp_path, SWAP, today)
    # Then the stack's own version again, so it is active and current becomes the previous one.
    for i, version in enumerate((oldest, older, current, stack)):
        activate(conn, version, at=_at(today - timedelta(days=30 - i), 12))
    with pytest.raises(ImportFailed):
        import_source(
            conn, make_zip(tmp_path / "bad.zip", drop="trips.txt"), "test:bad", today=today
        )
    failed = _one(conn, "SELECT version_id FROM schedule.versions WHERE source_url = 'test:bad'")
    conn.execute(
        "UPDATE schedule.versions SET created_at = now() - interval '8 days' WHERE version_id = %s",
        (failed,),
    )

    assert prune(conn, today) == sorted([oldest, older, failed])
    for version in (oldest, older, failed):
        assert _status(conn, version) is None
    assert _one(conn, "SELECT count(*) FROM schedule.stop_times WHERE version_id = %s", older) == 0
    assert [_status(conn, v) for v in (stack, current, staged)] == ["active", "retired", "ready"]
    assert prune(conn, today) == []


def test_prune_keeps_the_versions_of_yesterday_and_today(
    conn: Conn, make_zip: ZipMaker, calendar_between: Calendar, tmp_path: Path
) -> None:
    """Swaps on two mornings in a row: the version of yesterday's trips is not deleted."""
    others = _one(conn, "SELECT count(*) FROM schedule.versions WHERE status <> 'active'")
    if others:
        pytest.skip("the dev stack has non-active versions that prune would delete")
    stack = _one(conn, "SELECT version_id FROM schedule.v_active_version")

    day = SWAP + timedelta(days=30)
    a, b, c = (
        _version(conn, make_zip, calendar_between, tmp_path, day - timedelta(days=n), day)
        for n in (60, 50, 40)
    )
    activate(conn, a, at=_at(day - timedelta(days=3), 12))  # serves day-2 and day-1
    activate(conn, b, at=_at(day - timedelta(days=1), 3, 30))  # serves day
    activate(conn, c, at=_at(day, 3, 30))  # serves day+1 on
    # The stack's version last, so it is active again and c is the previous one.
    activate(conn, stack, at=_at(day + timedelta(days=5), 12))

    # On `day`, a serves yesterday's service day and b today's: both stay.
    assert prune(conn, day) == []
    assert [_status(conn, v) for v in (a, b, c)] == ["retired"] * 3
    # Ten days on, neither is in use: only the previous one (c) stays.
    assert prune(conn, day + timedelta(days=10)) == sorted([a, b])
    assert _status(conn, c) == "retired"


def test_failed_refresh_leaves_the_active_version(
    conn: Conn, make_zip: ZipMaker, tmp_path: Path
) -> None:
    """`btp-schedule refresh` with a broken zip exits 1 and the active version stays."""
    active = _one(conn, "SELECT version_id FROM schedule.v_active_version")
    assert main(["refresh", "--file", str(make_zip(tmp_path / "bad.zip", drop="stops.txt"))]) == 1
    assert _one(conn, "SELECT version_id FROM schedule.v_active_version") == active


def test_migration_records_the_active_version(conn: Conn) -> None:
    """An active version without an activation row (made before #24) gets one."""
    active = _one(conn, "SELECT version_id FROM schedule.v_active_version")
    conn.execute("DELETE FROM schedule.version_activations WHERE deactivated_at IS NULL")
    apply_schema(conn)
    activated_at, effective = conn.execute(
        "SELECT activated_at, effective_from FROM schedule.version_activations "
        "WHERE version_id = %s AND deactivated_at IS NULL",
        (active,),
    ).fetchone() or (None, None)
    assert activated_at is not None and effective == activated_at
