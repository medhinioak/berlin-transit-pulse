"""Unit tests for parse.py: messages from the recorded fixtures map to the right rows."""

from collections.abc import Callable
from datetime import UTC, date, datetime

import pytest
from google.transit.gtfs_realtime_pb2 import TripDescriptor, TripUpdate

from btp_common.fixtures import load_rt_fixture
from btp_processor.parse import (
    Removal,
    Skipped,
    Update,
    feed_ts_from_headers,
    parse_message,
    parse_service_date,
)

FEED_TS = 1795030000
HEADERS = [
    ("btp-feed-ts", str(FEED_TS).encode()),
    ("btp-snapshot", b"etag"),
    ("btp-event", b"changed"),
    ("btp-schema", b"gtfs-rt.TripUpdate/2.0"),
]
AT = datetime.fromtimestamp(FEED_TS, tz=UTC)


def _first(predicate: Callable[[TripUpdate], bool]) -> TripUpdate:
    """Return the first fixture trip for which predicate(tu) is true."""
    for snapshot in load_rt_fixture("rush-hour"):
        for entity in snapshot.entity:
            if predicate(entity.trip_update):
                return entity.trip_update
    raise AssertionError("no such trip in the fixtures")


def _parse(tu: TripUpdate) -> Update:
    """Parse a TripUpdate as a message and assert it is an Update."""
    key = f"{tu.trip.trip_id}|{tu.trip.start_date}".encode()
    parsed = parse_message(key, tu.SerializeToString(), HEADERS)
    assert isinstance(parsed, Update), parsed
    return parsed


def test_scheduled_trip_maps_to_trip_and_stop_rows() -> None:
    """The first fixture trip: trip fields, one stop row per stop_time_update, UTC times."""
    tu = load_rt_fixture("evening")[0].entity[0].trip_update
    parsed = _parse(tu)
    assert parsed.trip.trip_id == "311714762"
    assert parsed.trip.service_date == date(2026, 11, 18)
    assert parsed.trip.route_id == "19715_700"
    assert parsed.trip.start_time is None  # VBB sends no start_time
    assert parsed.trip.schedule_relationship == "SCHEDULED"
    assert parsed.trip.feed_ts == AT
    assert len(parsed.stops) == len(tu.stop_time_update)
    first = parsed.stops[0]
    assert first.stop_sequence == 0
    assert first.stop_id == "de:12052:900470000::15"
    assert first.arrival_time == datetime.fromtimestamp(1795030140, tz=UTC)
    assert first.arrival_time.tzinfo is UTC
    assert first.departure_time == first.arrival_time
    assert first.arrival_delay is None and first.departure_delay is None
    assert first.stop_schedule_relationship == "SCHEDULED"
    assert first.feed_ts == AT


def test_delays_are_kept_when_present() -> None:
    """A stop with an arrival delay keeps it in seconds; an absent delay is None, not 0."""
    tu = _first(lambda t: any(s.arrival.HasField("delay") for s in t.stop_time_update))
    parsed = _parse(tu)
    by_seq = {s.stop_sequence: s for s in parsed.stops}
    for stu in tu.stop_time_update:
        if stu.HasField("stop_sequence"):
            row = by_seq[stu.stop_sequence]
            expected = stu.arrival.delay if stu.arrival.HasField("delay") else None
            assert row.arrival_delay == expected


def test_cancelled_trip() -> None:
    """A cancelled trip keeps CANCELED as its relationship."""
    tu = _first(lambda t: t.trip.schedule_relationship == TripDescriptor.CANCELED)
    parsed = _parse(tu)
    assert parsed.trip.schedule_relationship == "CANCELED"
    assert len(parsed.stops) == sum(s.HasField("stop_sequence") for s in tu.stop_time_update)


def test_skipped_stop_keeps_its_relationship_and_missing_times() -> None:
    """A SKIPPED stop is stored as SKIPPED; a time it doesn't carry is None."""
    skipped = TripUpdate.StopTimeUpdate.SKIPPED
    tu = _first(
        lambda t: any(
            s.schedule_relationship == skipped and s.HasField("stop_sequence")
            for s in t.stop_time_update
        )
    )
    rows = {s.stop_sequence: s for s in _parse(tu).stops}
    for stu in tu.stop_time_update:
        if stu.schedule_relationship == skipped and stu.HasField("stop_sequence"):
            row = rows[stu.stop_sequence]
            assert row.stop_schedule_relationship == "SKIPPED"
            assert (row.arrival_time is None) == (not stu.arrival.HasField("time"))


def test_stops_without_stop_sequence_are_counted_not_stored() -> None:
    """Fixture trips with stop_id-only updates: those stops are skipped and counted."""
    tu = _first(lambda t: any(not s.HasField("stop_sequence") for s in t.stop_time_update))
    parsed = _parse(tu)
    missing = sum(not s.HasField("stop_sequence") for s in tu.stop_time_update)
    assert parsed.stops_without_sequence == missing > 0
    assert len(parsed.stops) == len(tu.stop_time_update) - missing


def test_every_fixture_message_parses() -> None:
    """All trips of all fixture snapshots parse; stop counts add up."""
    for label in ("rush-hour", "evening"):
        for snapshot in load_rt_fixture(label):
            for entity in snapshot.entity:
                tu = entity.trip_update
                parsed = _parse(tu)
                assert len(parsed.stops) + parsed.stops_without_sequence == len(tu.stop_time_update)


def test_tombstone_is_a_removal() -> None:
    """A null value is a removal of the trip in the key, at the header's feed time."""
    parsed = parse_message(b"279146123|20261118", None, HEADERS)
    assert parsed == Removal("279146123", date(2026, 11, 18), AT)


def test_tombstone_trip_id_may_contain_the_separator() -> None:
    """The key splits at the last '|', so a trip_id containing '|' survives."""
    parsed = parse_message(b"a|b|20261118", None, HEADERS)
    assert isinstance(parsed, Removal)
    assert parsed.trip_id == "a|b"


def test_missing_start_date_is_skipped() -> None:
    """No start_date: skipped with its own reason, for updates and tombstones."""
    tu = TripUpdate()
    tu.trip.trip_id = "1"
    assert parse_message(b"1|", tu.SerializeToString(), HEADERS) == Skipped("no_start_date", "1")
    assert parse_message(b"1|", None, HEADERS) == Skipped("no_start_date", "1")


def _no_trip_id() -> bytes:
    """Return a TripUpdate whose trip descriptor has a start_date but no trip_id."""
    tu = TripUpdate()
    tu.trip.start_date = "20261118"
    return tu.SerializeToString()


@pytest.mark.parametrize(
    ("key", "value", "headers"),
    [
        (b"1|20261118", b"\xff\xff\xff", HEADERS),  # not a protobuf
        (b"1|20261118", None, []),  # no btp-feed-ts
        (b"1|20261118", None, [("btp-feed-ts", b"soon")]),
        (b"no-separator", None, HEADERS),
        (None, None, HEADERS),
        (b"1|2026-11-18", None, HEADERS),
        (b"1|20261340", None, HEADERS),  # month 13
        (b"|20261118", None, HEADERS),
        (b"1|20261118", TripUpdate().SerializePartialToString(), HEADERS),  # no trip
        (b"|20261118", _no_trip_id(), HEADERS),
    ],
)
def test_malformed_messages_are_skipped_as_invalid(
    key: bytes | None, value: bytes | None, headers: list[tuple[str, bytes]]
) -> None:
    """Malformed messages never raise: they come back as Skipped('invalid')."""
    parsed = parse_message(key, value, headers)
    assert isinstance(parsed, Skipped)
    assert parsed.reason == "invalid"


def test_headers_as_str_or_dict() -> None:
    """Header values may be str, and headers may come as a dict."""
    assert feed_ts_from_headers([("btp-feed-ts", str(FEED_TS))]) == AT
    assert feed_ts_from_headers({"btp-feed-ts": str(FEED_TS).encode()}) == AT


def test_parse_service_date() -> None:
    """YYYYMMDD parses; empty means missing."""
    assert parse_service_date("20261231") == date(2026, 12, 31)
    assert parse_service_date("") is None


def test_time_zero_is_missing_not_the_epoch() -> None:
    """A StopTimeEvent with time 0 but a delay keeps the delay and stores no time."""
    tu = TripUpdate()
    tu.trip.trip_id = "1"
    tu.trip.start_date = "20261118"
    stu = tu.stop_time_update.add()
    stu.stop_sequence = 1
    stu.arrival.time = 0
    stu.arrival.delay = 60
    stop = _parse(tu).stops[0]
    assert stop.arrival_time is None
    assert stop.arrival_delay == 60
