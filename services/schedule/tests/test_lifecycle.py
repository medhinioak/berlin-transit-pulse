"""Staging, activation and pruning rules (btp_schedule.lifecycle)."""

from datetime import UTC, date, datetime, timedelta

from btp_schedule.lifecycle import (
    BERLIN,
    Staged,
    VersionState,
    activate_on_for,
    berlin_today,
    due_version,
    effective_from,
    prune_candidates,
    start_of_day,
    superseded,
)

SWAP = date(2026, 12, 13)
IMPORTED = datetime(2026, 11, 25, 3, 30, tzinfo=UTC)


def test_berlin_day_boundary() -> None:
    """23:30 UTC is already the next day in Berlin, in winter (+1 h) and in summer (+2 h)."""
    assert berlin_today(datetime(2026, 12, 12, 22, 59, tzinfo=UTC)) == date(2026, 12, 12)
    assert berlin_today(datetime(2026, 12, 12, 23, 0, tzinfo=UTC)) == SWAP
    assert berlin_today(datetime(2027, 6, 30, 21, 59, tzinfo=UTC)) == date(2027, 6, 30)
    assert berlin_today(datetime(2027, 6, 30, 22, 0, tzinfo=UTC)) == date(2027, 7, 1)


def test_start_of_day_on_dst_change() -> None:
    """Midnight before a DST change still has the day's own offset (+1 h on 28 Mar 2027)."""
    assert start_of_day(date(2027, 3, 28)) == datetime(2027, 3, 27, 23, 0, tzinfo=UTC)
    assert start_of_day(date(2027, 3, 29)) == datetime(2027, 3, 28, 22, 0, tzinfo=UTC)
    assert start_of_day(SWAP) == datetime(2026, 12, 12, 23, 0, tzinfo=UTC)


def test_activate_on_only_for_today_or_later() -> None:
    """A version that started in the past (a mid-cycle republish) isn't staged."""
    today = date(2026, 12, 1)
    assert activate_on_for(SWAP, today) == SWAP
    assert activate_on_for(today, today) == today
    assert activate_on_for(date(2026, 11, 18), today) is None
    assert activate_on_for(None, today) is None


def test_effective_from_on_the_eve() -> None:
    """Activated at 23:40 the evening before, the version applies from then."""
    eve = datetime(2026, 12, 12, 23, 40, tzinfo=BERLIN)
    assert effective_from(eve, SWAP, first_activation=True) == eve


def test_effective_from_when_late() -> None:
    """Activated on the day or later, a staged version still applies from 00:00 of its start."""
    late = datetime(2026, 12, 13, 9, 15, tzinfo=BERLIN)
    assert effective_from(late, SWAP, first_activation=True) == start_of_day(SWAP)
    assert effective_from(late + timedelta(days=2), SWAP, True) == start_of_day(SWAP)


def test_effective_from_for_rollback_and_unstaged() -> None:
    """Re-activations and versions without activate_on apply from the activation itself."""
    later = datetime(2027, 1, 5, 10, 0, tzinfo=BERLIN)
    assert effective_from(later, SWAP, first_activation=False) == later
    assert effective_from(later, None, first_activation=True) == later


def test_due_version_on_the_eve_only() -> None:
    """activate-due picks a version starting tomorrow, not one starting the day after."""
    staged = [Staged("new", SWAP, IMPORTED)]
    assert due_version(staged, date(2026, 12, 11)) is None
    assert due_version(staged, date(2026, 12, 12)) == "new"


def test_due_version_catches_up() -> None:
    """A version whose start has passed is due; of several, the latest start wins."""
    staged = [Staged("old", date(2026, 12, 1), IMPORTED), Staged("new", SWAP, IMPORTED)]
    assert due_version(staged, date(2026, 12, 14)) == "new"
    assert due_version(staged, date(2026, 12, 5)) == "old"
    assert due_version([], date(2026, 12, 14)) is None


def test_due_version_same_start_newest_import_wins() -> None:
    """A corrected republish with the same start date beats the original, whatever its hash."""
    staged = [
        Staged("f3aaaaaaaaaa", SWAP, IMPORTED),
        Staged("1abbbbbbbbbb", SWAP, IMPORTED + timedelta(days=7)),
    ]
    assert due_version(staged, date(2026, 12, 12)) == "1abbbbbbbbbb"


def test_superseded_staged_versions() -> None:
    """Activating a version retires the staged ones starting no later; later ones stay."""
    staged = [
        Staged("dec01", date(2026, 12, 1), IMPORTED),
        Staged("dec13", SWAP, IMPORTED),
        Staged("jan10", date(2027, 1, 10), IMPORTED),
    ]
    # The 13 Dec version caught up on the 14th: the 1 Dec one must never come back.
    assert superseded(staged, SWAP, date(2026, 12, 14)) == ["dec01", "dec13"]
    # A republish without activate_on, activated on the 14th: overdue ones go, January stays.
    assert superseded(staged, None, date(2026, 12, 14)) == ["dec01", "dec13"]
    # The same republish on 5 Dec: 13 Dec is still a future timetable.
    assert superseded(staged, None, date(2026, 12, 5)) == ["dec01"]


def test_effective_from_never_before_an_earlier_activation() -> None:
    """A backdated catch-up after a direct activation can't sort before it."""
    direct = datetime(2026, 12, 15, 10, 0, tzinfo=BERLIN)
    late = datetime(2026, 12, 15, 23, 40, tzinfo=BERLIN)
    assert effective_from(late, SWAP, True, not_before=direct) == direct
    assert effective_from(late, SWAP, True, not_before=None) == start_of_day(SWAP)


def test_due_version_morning_refresh_never_activates_early() -> None:
    """With lead_days=0 a version starting tomorrow waits for the eve."""
    staged = [Staged("new", SWAP, IMPORTED)]
    assert due_version(staged, date(2026, 12, 12), lead_days=0) is None
    assert due_version(staged, SWAP, lead_days=0) == "new"


NOW = datetime(2026, 12, 20, 3, 30, tzinfo=UTC)


def _v(
    version_id: str, status: str, days_ago: int, deactivated_days_ago: int | None = None
) -> VersionState:
    """A version created days_ago, optionally deactivated deactivated_days_ago."""
    deactivated = (
        None if deactivated_days_ago is None else NOW - timedelta(days=deactivated_days_ago)
    )
    return VersionState(version_id, status, NOW - timedelta(days=days_ago), deactivated)


def test_prune_keeps_active_previous_and_staged() -> None:
    """Older retired and old failed versions go; active, previous, ready and recent stay."""
    versions = [
        _v("active", "active", 10),
        _v("previous", "retired", 40, deactivated_days_ago=7),
        _v("older", "retired", 80, deactivated_days_ago=40),
        _v("oldest", "retired", 120, deactivated_days_ago=80),
        _v("staged", "ready", 2),
        _v("importing", "importing", 0),
        _v("failed-old", "failed", 8),
        _v("failed-new", "failed", 1),
    ]
    assert prune_candidates(versions, NOW) == ["failed-old", "older", "oldest"]


def test_prune_previous_is_the_last_deactivated() -> None:
    """The previous version is the one deactivated last, not the one created last."""
    versions = [
        _v("active", "active", 1),
        _v("rolled-back-to-then-left", "retired", 90, deactivated_days_ago=1),
        _v("newer-created", "retired", 30, deactivated_days_ago=5),
    ]
    assert prune_candidates(versions, NOW) == ["newer-created"]


def test_prune_keeps_versions_in_use() -> None:
    """A retired version still serving yesterday's service day is kept."""
    versions = [
        _v("c", "active", 1),
        _v("b", "retired", 2, deactivated_days_ago=1),
        _v("a", "retired", 3, deactivated_days_ago=2),
    ]
    assert prune_candidates(versions, NOW) == ["a"]
    assert prune_candidates(versions, NOW, in_use={"a", "b"}) == []


def test_prune_nothing_to_do() -> None:
    """A single active version is kept."""
    assert prune_candidates([_v("active", "active", 1)], NOW) == []
