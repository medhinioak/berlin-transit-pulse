"""Tests for trip classification and deterministic selection in the trim script."""

import pytest
from google.transit.gtfs_realtime_pb2 import FeedEntity, FeedMessage, TripDescriptor
from trim_fixtures import category, mode_for_route_type, phase, select, trimmed

T0 = 1_795_000_000


def _feed(at: int, trips: dict[str, tuple[int, int] | None], route: str = "r1") -> FeedMessage:
    """Build a snapshot: trip_id -> (first, last) stop time, or None for a cancelled trip."""
    feed = FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    feed.header.timestamp = at
    for trip_id, span in trips.items():
        entity = feed.entity.add(id=trip_id)
        trip = entity.trip_update.trip
        trip.trip_id, trip.start_date, trip.route_id = trip_id, "20261118", route
        if span is None:
            trip.schedule_relationship = TripDescriptor.CANCELED
            continue
        for seq, t in enumerate(span):
            stu = entity.trip_update.stop_time_update.add(stop_sequence=seq, stop_id=f"s{seq}")
            stu.arrival.time = t
    return feed


def _entity(span: tuple[int, int] | None) -> FeedEntity:
    """Return the single entity of a one-trip snapshot."""
    entity: FeedEntity = _feed(T0, {"t": span}).entity[0]
    return entity


@pytest.mark.parametrize(
    ("span", "expected"),
    [
        ((T0 + 60, T0 + 600), "future"),
        ((T0 - 60, T0 + 600), "running"),
        ((T0 - 600, T0 - 60), "finished"),
        (None, "cancelled"),
    ],
)
def test_phase(span: tuple[int, int] | None, expected: str) -> None:
    """A trip's phase depends on its stop times relative to the feed timestamp."""
    assert phase(_entity(span), T0) == expected


@pytest.mark.parametrize(
    ("phases", "expected"),
    [
        ([None, "running", "running"], "appears"),
        (["running", "running", None], "disappears"),
        (["running", None, "running"], None),
        (["running", "cancelled", "cancelled"], "cancelled"),
        (["future", "future", "running"], "starts"),
        (["running", "running", "finished"], "ends"),
        (["running", "running", "running"], "running"),
        (["future", "future", "future"], "future"),
        (["finished", "finished", "finished"], None),
    ],
)
def test_category(phases: list[str | None], expected: str | None) -> None:
    """Each trip falls into at most one category, edge cases first."""
    assert category(phases) == expected


@pytest.mark.parametrize(
    ("route_type", "mode"),
    [
        (109, "sbahn"),
        (400, "ubahn"),
        (700, "bus"),
        (900, "tram"),
        (106, "regional"),
        (1000, "ferry"),
    ],
)
def test_mode_for_extended_route_types(route_type: int, mode: str) -> None:
    """VBB's extended route types map to modes; 109 is S-Bahn, not regional rail."""
    assert mode_for_route_type(route_type) == mode


def _snapshots() -> list[FeedMessage]:
    """Two snapshots: 20 running trips, one cancelled, one appearing, one vanishing."""
    running = {f"run{i:02d}": (T0 - 60, T0 + 3600) for i in range(20)}
    first = _feed(T0, {**running, "cxl": None, "gone": (T0 - 60, T0 + 3600)})
    second = _feed(T0 + 30, {**running, "cxl": None, "new": (T0, T0 + 3600)})
    return [first, second]


def test_select_is_deterministic_and_respects_quotas() -> None:
    """The same input and seed give the same picks; quotas cap each category."""
    feeds = _snapshots()
    quotas = {
        "per_mode": {"running": 5},
        "edge_cases": {"cancelled": 1, "appears": 1, "disappears": 1},
    }
    a = select(feeds, {"r1": "bus"}, seed="x", **quotas)
    b = select(feeds, {"r1": "bus"}, seed="x", **quotas)
    assert a.picks == b.picks
    assert a.counts() == {
        "appears": {"bus": 1},
        "cancelled": {"bus": 1},
        "disappears": {"bus": 1},
        "running": {"bus": 5},
    }


def test_trimmed_keeps_header_and_only_chosen_trips() -> None:
    """The trimmed snapshot is a valid feed with the original header and the chosen trips."""
    feed = _snapshots()[0]
    small = trimmed(feed, {("run03", "20261118"), ("cxl", "20261118")})
    assert small.header == feed.header
    assert sorted(e.trip_update.trip.trip_id for e in small.entity) == ["cxl", "run03"]
