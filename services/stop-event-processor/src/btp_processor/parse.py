"""Turn one trip-updates message into rows to store (pure functions, no Kafka or Postgres).

The message contract is schemas/trip-updates.md: key "{trip_id}|{start_date}", value a
serialized GTFS-RT TripUpdate or null (tombstone), headers btp-feed-ts and friends.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Literal

from google.protobuf.message import DecodeError
from google.transit.gtfs_realtime_pb2 import TripDescriptor, TripUpdate

HeaderValue = str | bytes | None
# What confluent_kafka's Message.headers() returns: a list of pairs (or, rarely, a dict).
Headers = Sequence[tuple[str, HeaderValue]] | Mapping[str, HeaderValue]
SkipReason = Literal["no_start_date", "invalid"]


@dataclass(frozen=True)
class TripRow:
    """One row of events.trip_state, as written by a new or changed message."""

    trip_id: str
    service_date: date
    route_id: str | None
    start_time: str | None
    schedule_relationship: str
    feed_ts: datetime


@dataclass(frozen=True)
class StopRow:
    """One row of events.stop_predictions."""

    trip_id: str
    service_date: date
    stop_sequence: int
    stop_id: str | None
    arrival_time: datetime | None
    arrival_delay: int | None
    departure_time: datetime | None
    departure_delay: int | None
    stop_schedule_relationship: str
    feed_ts: datetime


@dataclass(frozen=True)
class Update:
    """A new or changed trip: its state, its stops, and how many stops had no stop_sequence."""

    trip: TripRow
    stops: list[StopRow] = field(default_factory=list)
    stops_without_sequence: int = 0


@dataclass(frozen=True)
class Removal:
    """A tombstone: the trip is no longer in the feed as of feed_ts."""

    trip_id: str
    service_date: date
    feed_ts: datetime


@dataclass(frozen=True)
class Skipped:
    """A message that can't be stored, and why."""

    reason: SkipReason
    detail: str


Parsed = Update | Removal | Skipped


def parse_service_date(value: str) -> date | None:
    """Return the date of a GTFS YYYYMMDD string, or None if it is empty."""
    if not value:
        return None
    if len(value) != 8 or not value.isdigit():
        raise ValueError(f"start_date {value!r} is not YYYYMMDD")
    return date(int(value[:4]), int(value[4:6]), int(value[6:]))


def _header(headers: Headers | None, name: str) -> bytes | None:
    """Return the value of the first header called name, as bytes."""
    pairs = headers.items() if isinstance(headers, Mapping) else headers or ()
    for key, value in pairs:
        if key == name:
            return value.encode() if isinstance(value, str) else value
    return None


def feed_ts_from_headers(headers: Headers | None) -> datetime:
    """Return the btp-feed-ts header (epoch seconds) as a UTC datetime."""
    raw = _header(headers, "btp-feed-ts")
    if raw is None:
        raise ValueError("no btp-feed-ts header")
    return datetime.fromtimestamp(int(raw.decode()), tz=UTC)


def _time(event: TripUpdate.StopTimeEvent, present: bool) -> datetime | None:
    """Return the event's time as a UTC datetime; None if absent or 0 (the epoch is no time)."""
    if not present or not event.HasField("time") or event.time == 0:
        return None
    return datetime.fromtimestamp(event.time, tz=UTC)


def _update(tu: TripUpdate, service_date: date, feed_ts: datetime) -> Update:
    """Return the trip row and stop rows of a decoded TripUpdate."""
    trip = tu.trip
    row = TripRow(
        trip_id=trip.trip_id,
        service_date=service_date,
        route_id=trip.route_id or None,
        start_time=trip.start_time or None,
        schedule_relationship=TripDescriptor.ScheduleRelationship.Name(trip.schedule_relationship),
        feed_ts=feed_ts,
    )
    stops: list[StopRow] = []
    without_sequence = 0
    for stu in tu.stop_time_update:
        # M1 keys stops by stop_sequence only; stop_id-only updates are counted for M3.
        if not stu.HasField("stop_sequence"):
            without_sequence += 1
            continue
        arrival, departure = stu.arrival, stu.departure
        has_arr, has_dep = stu.HasField("arrival"), stu.HasField("departure")
        stops.append(
            StopRow(
                trip_id=trip.trip_id,
                service_date=service_date,
                stop_sequence=stu.stop_sequence,
                stop_id=stu.stop_id or None,
                arrival_time=_time(arrival, has_arr),
                arrival_delay=arrival.delay if has_arr and arrival.HasField("delay") else None,
                departure_time=_time(departure, has_dep),
                departure_delay=(
                    departure.delay if has_dep and departure.HasField("delay") else None
                ),
                stop_schedule_relationship=TripUpdate.StopTimeUpdate.ScheduleRelationship.Name(
                    stu.schedule_relationship
                ),
                feed_ts=feed_ts,
            )
        )
    return Update(row, stops, without_sequence)


def parse_message(key: bytes | None, value: bytes | None, headers: Headers | None) -> Parsed:
    """Return what one message means for the tables, or why it is skipped.

    A value is a full TripUpdate (new and changed are treated alike); a null value is a
    tombstone for the trip in the key. Never raises: malformed messages come back as Skipped.
    """
    try:
        feed_ts = feed_ts_from_headers(headers)
        if value is None:
            trip_id, sep, start_date = (key or b"").decode().rpartition("|")
            if not sep or not trip_id:
                raise ValueError(f"tombstone key {key!r} is not trip_id|start_date")
            service_date = parse_service_date(start_date)
            if service_date is None:
                return Skipped("no_start_date", trip_id)
            return Removal(trip_id, service_date, feed_ts)

        tu = TripUpdate.FromString(value)
        if not tu.trip.trip_id:
            raise ValueError("TripUpdate has no trip_id")
        service_date = parse_service_date(tu.trip.start_date)
        if service_date is None:
            return Skipped("no_start_date", tu.trip.trip_id)
        return _update(tu, service_date, feed_ts)
    except (ValueError, DecodeError, OverflowError, OSError) as exc:
        return Skipped("invalid", f"{type(exc).__name__}: {exc}")
