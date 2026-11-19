"""Write parsed messages to events.trip_state and events.stop_predictions.

Every write is guarded by feed_ts: a row is changed only by a message whose feed_ts is >= the
stored one. So each row ends up as the newest message that touched it, whatever the order,
batching or number of deliveries (ties go to the message delivered last).
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from importlib.resources import files
from itertools import groupby
from typing import Any

import psycopg

from btp_processor.parse import Removal, StopRow, Update

Connection = psycopg.Connection[Any]

UPSERT_TRIP = """
INSERT INTO events.trip_state AS t
    (trip_id, service_date, route_id, start_time, schedule_relationship, feed_ts)
VALUES (%s, %s, %s, %s, %s, %s)
ON CONFLICT (trip_id, service_date) DO UPDATE SET
    route_id = excluded.route_id,
    start_time = excluded.start_time,
    schedule_relationship = excluded.schedule_relationship,
    feed_ts = excluded.feed_ts,
    removed_at = NULL,
    updated_at = now()
WHERE excluded.feed_ts >= t.feed_ts
"""

# A tombstone keeps the trip's last state and only marks when it left the feed. It moves
# feed_ts too, so an older update delivered later can't clear removed_at again. A tombstone
# for a trip that was never stored changes nothing.
REMOVE_TRIP = """
UPDATE events.trip_state
SET removed_at = %(ts)s, feed_ts = %(ts)s, updated_at = now()
WHERE trip_id = %(trip_id)s AND service_date = %(service_date)s AND feed_ts <= %(ts)s
"""

STOP_COLUMNS = (
    "trip_id",
    "service_date",
    "stop_sequence",
    "stop_id",
    "arrival_time",
    "arrival_delay",
    "departure_time",
    "departure_delay",
    "stop_schedule_relationship",
    "feed_ts",
)
STOP_TYPES = (
    "text",
    "date",
    "integer",
    "text",
    "timestamptz",
    "integer",
    "timestamptz",
    "integer",
    "text",
    "timestamptz",
)

# One statement per batch: the rows arrive as one array per column. The rows must have
# distinct keys (ON CONFLICT can't change a row twice in one statement), see latest_stops.
UPSERT_STOPS = f"""
INSERT INTO events.stop_predictions AS s ({", ".join(STOP_COLUMNS)})
SELECT * FROM unnest({", ".join(f"%s::{t}[]" for t in STOP_TYPES)})
ON CONFLICT (trip_id, service_date, stop_sequence) DO UPDATE SET
    {", ".join(f"{c} = excluded.{c}" for c in STOP_COLUMNS[3:])},
    updated_at = now()
WHERE excluded.feed_ts >= s.feed_ts
"""


@dataclass
class StoreResult:
    """Rows written and messages or rows ignored because a newer one was already stored."""

    trips_written: int = 0
    trips_ignored_older: int = 0
    removals_applied: int = 0
    removals_ignored: int = 0  # older than the stored state, or the trip was never stored
    stops_written: int = 0
    stops_ignored_older: int = 0


def apply_schema(conn: Connection) -> None:
    """Create the events tables if they don't exist."""
    for resource in sorted(files("btp_processor").joinpath("sql").iterdir(), key=lambda r: r.name):
        if resource.name.endswith(".sql"):
            conn.execute(resource.read_text(encoding="utf-8").encode())


def latest_stops(rows: Iterable[StopRow]) -> tuple[list[StopRow], int]:
    """Keep one row per stop key: the newest feed_ts, the later row on a tie.

    Returns the rows and how many were dropped for being older than another row of the same
    stop. Rows replaced by an equal or newer one aren't counted: applying them one by one
    would give the same result.
    """
    latest: dict[tuple[str, object, int], StopRow] = {}
    older = 0
    for row in rows:
        key = (row.trip_id, row.service_date, row.stop_sequence)
        kept = latest.get(key)
        if kept is not None and row.feed_ts < kept.feed_ts:
            older += 1
        else:
            latest[key] = row
    return list(latest.values()), older


def stop_columns(rows: Sequence[StopRow]) -> list[list[object]]:
    """Return the rows as one list per column, in STOP_COLUMNS order."""
    return [[getattr(row, c) for row in rows] for c in STOP_COLUMNS]


def apply_batch(conn: Connection, items: Sequence[Update | Removal]) -> StoreResult:
    """Write a batch in one transaction and return the counts.

    Trip writes run one statement per message in delivery order, so updates and tombstones of
    the same trip interleave exactly as delivered. Consecutive messages of the same kind go
    through one executemany (pipelined, one round trip per run). Stops go in one statement.
    """
    result = StoreResult()
    stops: list[StopRow] = []
    with conn.transaction(), conn.cursor() as cur:
        for is_update, run in groupby(items, key=lambda item: isinstance(item, Update)):
            group = list(run)
            if is_update:
                updates = [item for item in group if isinstance(item, Update)]
                cur.executemany(
                    UPSERT_TRIP,
                    [
                        (
                            u.trip.trip_id,
                            u.trip.service_date,
                            u.trip.route_id,
                            u.trip.start_time,
                            u.trip.schedule_relationship,
                            u.trip.feed_ts,
                        )
                        for u in updates
                    ],
                )
                result.trips_written += cur.rowcount
                result.trips_ignored_older += len(updates) - cur.rowcount
                for u in updates:
                    stops.extend(u.stops)
            else:
                removals = [item for item in group if isinstance(item, Removal)]
                cur.executemany(
                    REMOVE_TRIP,
                    [
                        {"trip_id": r.trip_id, "service_date": r.service_date, "ts": r.feed_ts}
                        for r in removals
                    ],
                )
                result.removals_applied += cur.rowcount
                result.removals_ignored += len(removals) - cur.rowcount

        rows, result.stops_ignored_older = latest_stops(stops)
        if rows:
            cur.execute(UPSERT_STOPS, stop_columns(rows))
            result.stops_written = cur.rowcount
            result.stops_ignored_older += len(rows) - cur.rowcount
    return result
