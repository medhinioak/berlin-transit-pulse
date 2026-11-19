"""Tests for snapshot diffing on the recorded fixtures."""

import pytest
from google.transit.gtfs_realtime_pb2 import FeedMessage, TripUpdate

from btp_common.fixtures import load_rt_fixture
from btp_ingest.diff import diff, fingerprint, trip_key


def _without_timestamps(message: FeedMessage) -> dict[tuple[str, str], TripUpdate]:
    """Map (trip_id, start_date) to the trip update with its timestamp cleared, last wins."""
    trips: dict[tuple[str, str], TripUpdate] = {}
    for entity in message.entity:
        tu = TripUpdate()
        tu.CopyFrom(entity.trip_update)
        tu.ClearField("timestamp")
        trips[(tu.trip.trip_id, tu.trip.start_date)] = tu
    return trips


def _expected_counts(before: FeedMessage, after: FeedMessage) -> tuple[int, int, int]:
    """Count new, changed and removed trips by comparing messages, not fingerprints."""
    old, new = _without_timestamps(before), _without_timestamps(after)
    added = sum(1 for k in new if k not in old)
    changed = sum(1 for k in new if k in old and new[k] != old[k])
    removed = sum(1 for k in old if k not in new)
    return added, changed, removed


def _message(*updates: TripUpdate, ids: list[str] | None = None) -> FeedMessage:
    """Build a feed message with one entity per trip update."""
    message = FeedMessage()
    message.header.gtfs_realtime_version = "2.0"
    for i, tu in enumerate(updates):
        entity = message.entity.add()
        entity.id = ids[i] if ids else str(i)
        entity.trip_update.CopyFrom(tu)
    return message


def _trip(trip_id: str, start_date: str = "20261118", delay: int = 60) -> TripUpdate:
    """Return a trip update with one stop-time update."""
    tu = TripUpdate()
    tu.trip.trip_id = trip_id
    tu.trip.start_date = start_date
    tu.timestamp = 1_795_000_000
    stu = tu.stop_time_update.add()
    stu.stop_sequence = 1
    stu.arrival.delay = delay
    return tu


@pytest.mark.parametrize("label", ["rush-hour", "evening"])
def test_consecutive_fixture_snapshots(label: str) -> None:
    """Each step's counts match an independent comparison of the decoded trips."""
    snapshots = load_rt_fixture(label)
    first = diff({}, snapshots[0])
    assert len(first.new) == first.total == len(_without_timestamps(snapshots[0]))
    assert first.changed == [] and first.removed == []

    previous, state = snapshots[0], first.fingerprints
    for snapshot in snapshots[1:]:
        changes = diff(state, snapshot)
        expected = _expected_counts(previous, snapshot)
        assert (len(changes.new), len(changes.changed), len(changes.removed)) == expected
        assert changes.events < changes.total  # only a small part of the feed changes
        previous, state = snapshot, changes.fingerprints


def test_same_snapshot_twice_gives_no_changes() -> None:
    """Replaying a snapshot publishes nothing."""
    snapshot = load_rt_fixture("rush-hour")[0]
    state = diff({}, snapshot).fingerprints
    changes = diff(state, snapshot)
    assert changes.events == 0
    assert changes.change_rate_pct == 0.0


def test_timestamp_only_change_is_not_a_change() -> None:
    """A trip whose only difference is its timestamp is unchanged."""
    before = _trip("a")
    after = _trip("a")
    after.timestamp += 30
    state = diff({}, _message(before)).fingerprints
    assert diff(state, _message(after)).events == 0
    assert fingerprint(before) == fingerprint(after)


def test_new_entity_id_with_same_content_is_not_a_change() -> None:
    """VBB changes FeedEntity.id when content changes; the key must not depend on it."""
    state = diff({}, _message(_trip("a"), ids=["x1"])).fingerprints
    assert diff(state, _message(_trip("a"), ids=["x2"])).events == 0


def test_changed_delay_is_a_change() -> None:
    """A different delay on a known trip is reported as changed, not new."""
    state = diff({}, _message(_trip("a", delay=60))).fingerprints
    changes = diff(state, _message(_trip("a", delay=120)))
    assert [k for k, _ in changes.changed] == ["a|20261118"]
    assert changes.new == [] and changes.removed == []


def test_vanished_trip_gives_one_removal() -> None:
    """A trip missing from the next snapshot is removed once, then forgotten."""
    state = diff({}, _message(_trip("a"), _trip("b"))).fingerprints
    changes = diff(state, _message(_trip("a")))
    assert changes.removed == ["b|20261118"]
    assert diff(changes.fingerprints, _message(_trip("a"))).removed == []


def test_vanished_fixture_trip() -> None:
    """Dropping one entity from a real snapshot gives exactly that removal."""
    snapshot = load_rt_fixture("evening")[0]
    state = diff({}, snapshot).fingerprints
    trimmed = FeedMessage()
    trimmed.CopyFrom(snapshot)
    gone = trimmed.entity[0].trip_update
    key = trip_key(gone)
    del trimmed.entity[0]
    changes = diff(state, trimmed)
    assert changes.removed == [key]
    assert changes.new == [] and changes.changed == []


def test_duplicate_keys_keep_the_last_and_are_counted() -> None:
    """Two entities with the same key: the last one wins, one duplicate is counted."""
    changes = diff({}, _message(_trip("a", delay=60), _trip("a", delay=300), _trip("b")))
    assert changes.duplicates == 1
    assert changes.total == 2
    kept = dict(changes.new)["a|20261118"]
    assert kept.stop_time_update[0].arrival.delay == 300


def test_same_trip_on_another_service_day_is_a_separate_key() -> None:
    """start_date is part of the key, and an empty start_date keeps the separator."""
    changes = diff({}, _message(_trip("a", "20261118"), _trip("a", "20261119"), _trip("a", "")))
    assert sorted(k for k, _ in changes.new) == ["a|", "a|20261118", "a|20261119"]


def test_entities_without_trip_update_are_ignored() -> None:
    """Entities that carry something else (e.g. alerts) aren't trips."""
    message = _message(_trip("a"))
    message.entity.add().id = "alert-only"
    changes = diff({}, message)
    assert changes.total == 1
