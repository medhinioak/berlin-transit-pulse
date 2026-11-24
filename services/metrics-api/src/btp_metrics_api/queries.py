"""SQL the API runs. Only reads the marts schema, as the read-only role btp_api."""

from collections.abc import Collection
from datetime import datetime
from typing import Any

import psycopg
from psycopg.rows import dict_row

NOW_BY_MODE = """
    SELECT mode, running_trips, trips_with_nonzero_delay, avg_delay_s, p50_delay_s,
           p95_delay_s, cancelled_trips_today, latest_feed_ts, computed_at
    FROM marts.v_now_by_mode
    ORDER BY mode
"""

LATEST_FEED_TS = "SELECT latest_feed_ts FROM marts.v_feed_status"

SET_AS_OF = "SELECT set_config('btp.as_of', %s, false)"

SCHEDULE_COVERAGE = """
    SELECT (SELECT version_id FROM marts.v_schedule_status),
           (SELECT count(*) FROM marts.v_schedule_trips WHERE trip_id = ANY(%s))
"""


def now_by_mode(conn: psycopg.Connection[Any]) -> list[dict[str, Any]]:
    """Return the rows of marts.v_now_by_mode, one per mode."""
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(NOW_BY_MODE).fetchall()


def latest_feed_ts(conn: psycopg.Connection[Any]) -> datetime | None:
    """Return the newest feed time the processor applied, or None if there is no data."""
    row = conn.execute(LATEST_FEED_TS).fetchone()
    return row[0] if row else None


def set_as_of(conn: psycopg.Connection[Any], as_of: datetime) -> None:
    """Pin the views' reference time for this session (setting btp.as_of, ADR-0019)."""
    conn.execute(SET_AS_OF, [as_of.isoformat()])


def schedule_coverage(
    conn: psycopg.Connection[Any], trip_ids: Collection[str]
) -> tuple[str | None, int]:
    """Return the active schedule version (None if none) and how many trip_ids it contains."""
    row = conn.execute(SCHEDULE_COVERAGE, [list(trip_ids)]).fetchone()
    assert row is not None  # a SELECT without FROM always returns one row
    return row[0], row[1]
