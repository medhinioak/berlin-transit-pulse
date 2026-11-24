"""Integration tests for marts.v_now_by_mode on hand-made trips.

Needs `make up`. Writes trips as btp_processor on service days in 2030, which nothing else
uses, pins the view's reference time there with btp.as_of, reads as btp_api, and deletes
the rows afterwards. Trip IDs are real Berlin trips of the active schedule, so the view's
mode and Berlin join applies. Override passwords with the BTP_PG_*_PASSWORD variables.
"""

import os
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import psycopg
import pytest
from psycopg.rows import dict_row

from btp_metrics_jobs.cli import apply_sql, view_files
from btp_metrics_jobs.settings import MetricsJobsSettings

pytestmark = pytest.mark.integration

Connection = psycopg.Connection[Any]

D = date(2030, 1, 16)  # "today" in Europe/Berlin
REF = datetime(2030, 1, 15, 23, 30, tzinfo=UTC)  # 00:30 in Berlin on D (CET, UTC+1)
# In the past, so rows left behind by an interrupted run can't make /health look fresh.
FEED_TS = datetime(2020, 1, 1, tzinfo=UTC)


def at(minutes: int) -> datetime:
    """Return REF shifted by minutes."""
    return REF + timedelta(minutes=minutes)


def connect(user: str, password_var: str, default: str) -> Connection:
    """Connect to the compose Postgres as a service role."""
    return psycopg.connect(
        host=os.environ.get("BTP_PG_HOST", "localhost"),
        port=int(os.environ.get("BTP_PG_PORT", "5432")),
        dbname="btp",
        user=user,
        password=os.environ.get(password_var, default),
        autocommit=True,
    )


@pytest.fixture(scope="module")
def trips() -> dict[str, list[str]]:
    """Real trip IDs from the active schedule: Berlin bus, Berlin tram, non-Berlin bus."""
    with connect("btp_metrics_jobs", "BTP_PG_METRICS_JOBS_PASSWORD", "dev_metrics_jobs") as conn:
        apply_sql(conn, view_files(MetricsJobsSettings().metrics_sql_dir))

        def pick(mode: str, in_berlin: bool, n: int) -> list[str]:
            rows = conn.execute(
                """SELECT t.trip_id FROM schedule.v_trips t
                   JOIN schedule.v_routes r ON r.route_id = t.route_id
                   WHERE r.mode = %s AND t.in_berlin = %s ORDER BY t.trip_id LIMIT %s""",
                [mode, in_berlin, n],
            ).fetchall()
            assert len(rows) == n, f"active schedule has too few {mode} trips"
            return [r[0] for r in rows]

        return {
            "bus": pick("bus", True, 9),
            "tram": pick("tram", True, 1),
            "outside": pick("bus", False, 1),
        }


@pytest.fixture
def processor() -> Iterator[Connection]:
    """Connection as btp_processor; removes the 2030 rows before and after the test."""
    with connect("btp_processor", "BTP_PG_PROCESSOR_PASSWORD", "dev_processor") as conn:
        cleanup = [D - timedelta(days=1), D]

        def clean() -> None:
            conn.execute(
                "DELETE FROM events.stop_predictions WHERE service_date = ANY(%s)", [cleanup]
            )
            conn.execute("DELETE FROM events.trip_state WHERE service_date = ANY(%s)", [cleanup])

        clean()
        yield conn
        clean()


def add_trip(
    conn: Connection,
    trip_id: str,
    service_date: date,
    stops: list[tuple[datetime, int | None, int | None]],
    relationship: str = "SCHEDULED",
    removed: bool = False,
) -> None:
    """Insert a trip and its stops: (departure time, departure delay, arrival delay)."""
    conn.execute(
        """INSERT INTO events.trip_state
           (trip_id, service_date, schedule_relationship, feed_ts, removed_at)
           VALUES (%s, %s, %s, %s, %s)""",
        [trip_id, service_date, relationship, FEED_TS, FEED_TS if removed else None],
    )
    for seq, (ts, dep_delay, arr_delay) in enumerate(stops, start=1):
        conn.execute(
            """INSERT INTO events.stop_predictions
               (trip_id, service_date, stop_sequence, arrival_time, arrival_delay,
                departure_time, departure_delay, stop_schedule_relationship, feed_ts)
               VALUES (%s, %s, %s, %s, %s, %s, %s, 'SCHEDULED', %s)""",
            [trip_id, service_date, seq, ts, arr_delay, ts, dep_delay, FEED_TS],
        )


def skip(conn: Connection, trip_id: str, service_date: date, sequences: list[int]) -> None:
    """Mark stops of a trip as SKIPPED."""
    conn.execute(
        """UPDATE events.stop_predictions SET stop_schedule_relationship = 'SKIPPED'
           WHERE trip_id = %s AND service_date = %s AND stop_sequence = ANY(%s)""",
        [trip_id, service_date, sequences],
    )


def view_rows() -> dict[str, dict[str, Any]]:
    """Read the view as btp_api at REF, keyed by mode."""
    with connect("btp_api", "BTP_PG_API_PASSWORD", "dev_api") as conn:
        conn.execute("SELECT set_config('btp.as_of', %s, false)", [REF.isoformat()])
        with conn.cursor(row_factory=dict_row) as cur:
            return {r["mode"]: r for r in cur.execute("SELECT * FROM marts.v_now_by_mode")}


def test_running_trips_and_current_delay(
    processor: Connection, trips: dict[str, list[str]]
) -> None:
    """Only started, unfinished, Berlin, not removed or cancelled trips count."""
    bus = trips["bus"]
    yesterday = D - timedelta(days=1)
    # Past midnight: service day D-1, still running at 00:30. Next stop is seq 2: delay 60.
    add_trip(processor, bus[0], yesterday, [(at(-30), 300, None), (at(15), 60, 90), (at(40), 0, 0)])
    # No departure delay at the next stop: the arrival delay (0) is used.
    add_trip(processor, bus[1], D, [(at(-10), 120, None), (at(10), None, 0)])
    add_trip(processor, bus[2], D, [(at(20), 30, None), (at(50), 30, None)])  # not started
    add_trip(processor, bus[3], D, [(at(-60), 30, None), (at(-5), 30, None)])  # finished
    add_trip(processor, bus[4], D, [(at(-5), 500, None), (at(5), 500, None)], removed=True)
    add_trip(processor, bus[5], D, [(at(-5), 0, None), (at(5), 0, None)], relationship="CANCELED")
    # Cancelled on yesterday's service day: not "today".
    add_trip(processor, bus[6], yesterday, [(at(-5), 0, None)], relationship="CANCELED")
    # Outside Berlin, running: left out.
    add_trip(processor, trips["outside"][0], D, [(at(-5), 900, None), (at(5), 900, None)])
    # Next stop skipped: the current delay comes from the next served stop (90).
    add_trip(processor, bus[7], D, [(at(-10), 30, None), (at(5), 0, None), (at(15), 90, None)])
    skip(processor, bus[7], D, [2])
    # Only skipped stops left: finished as far as served stops go, not running.
    add_trip(processor, bus[8], D, [(at(-20), 30, None), (at(5), 0, None), (at(10), 0, None)])
    skip(processor, bus[8], D, [2, 3])
    # Running, but no delay at all at the next stop: counted, delay unknown.
    add_trip(processor, trips["tram"][0], D, [(at(-5), 60, None), (at(5), None, None)])

    rows = view_rows()
    assert set(rows) == {"bus", "tram"}
    b = rows["bus"]
    assert b["running_trips"] == 3  # bus[0], bus[1], bus[7]
    assert b["trips_with_nonzero_delay"] == 2
    assert b["avg_delay_s"] == 50.0  # [60, 0, 90]
    assert b["p50_delay_s"] == 60.0
    assert b["p95_delay_s"] == 87.0  # percentile_cont: 60 + 0.9 * (90 - 60)
    assert b["cancelled_trips_today"] == 1
    assert b["latest_feed_ts"] is not None
    t = rows["tram"]
    assert t["running_trips"] == 1
    assert t["trips_with_nonzero_delay"] == 0
    assert t["avg_delay_s"] is None
    assert t["cancelled_trips_today"] == 0


def test_mode_with_only_cancellations(processor: Connection, trips: dict[str, list[str]]) -> None:
    """A mode with cancellations today but nothing running still gets a row."""
    add_trip(processor, trips["tram"][0], D, [(at(60), 0, None)], relationship="CANCELED")
    tram = view_rows()["tram"]
    assert tram["running_trips"] == 0
    assert tram["avg_delay_s"] is None
    assert tram["cancelled_trips_today"] == 1


@pytest.fixture
def swapped_schedule() -> Iterator[str]:
    """Simulate a timetable change at 00:00 on D; yield the suffix of the new trip_ids.

    The new version is a copy of the active one whose trip_ids end in ':new'. Written as
    btp_schedule; removed afterwards, and the activation history restored. Only
    version_activations changes, as activate() would record it; the active-version views
    stay as they are.
    """
    new = "test-swap"
    with connect("btp_schedule", "BTP_PG_SCHEDULE_PASSWORD", "dev_schedule") as conn:

        def clean() -> None:
            with conn.transaction():
                for table in ("routes", "trips", "versions"):
                    conn.execute(f"DELETE FROM schedule.{table} WHERE version_id = %s", [new])
                conn.execute(
                    "DELETE FROM schedule.version_activations WHERE version_id = %s", [new]
                )
                conn.execute(
                    "UPDATE schedule.version_activations SET deactivated_at = NULL "
                    "WHERE activation_id = (SELECT max(activation_id) "
                    "FROM schedule.version_activations)"
                )

        clean()
        with conn.transaction():
            old = conn.execute("SELECT version_id FROM schedule.v_active_version").fetchone()
            assert old is not None
            conn.execute(
                "INSERT INTO schedule.versions (version_id, sha256, source_url, status) "
                "VALUES (%s, 'test', 'test:swap', 'ready')",
                [new],
            )
            # Only the columns the view reads; the rest stay NULL.
            conn.execute(
                "INSERT INTO schedule.routes (version_id, route_id, route_type, mode, in_berlin) "
                "SELECT %s, route_id, route_type, mode, in_berlin FROM schedule.routes "
                "WHERE version_id = %s",
                [new, old[0]],
            )
            conn.execute(
                "INSERT INTO schedule.trips (version_id, route_id, service_id, trip_id, in_berlin) "
                "SELECT %s, route_id, service_id, trip_id || ':new', in_berlin FROM schedule.trips "
                "WHERE version_id = %s",
                [new, old[0]],
            )
            midnight = datetime(D.year, D.month, D.day, tzinfo=ZoneInfo("Europe/Berlin"))
            conn.execute(
                "UPDATE schedule.version_activations SET deactivated_at = %s "
                "WHERE deactivated_at IS NULL",
                [midnight],
            )
            conn.execute(
                "INSERT INTO schedule.version_activations "
                "(version_id, activated_at, effective_from) VALUES (%s, %s, %s)",
                [new, midnight, midnight],
            )
        yield ":new"
        clean()


def test_timetable_change_night(
    processor: Connection, trips: dict[str, list[str]], swapped_schedule: str
) -> None:
    """At 00:30 on the first day of a new timetable, yesterday's trips keep their old version.

    The new version has new trip_ids: today's trips match only under those, while a trip of
    yesterday still running past midnight matches under its old id.
    """
    bus = trips["bus"]
    yesterday = D - timedelta(days=1)
    add_trip(processor, bus[0], yesterday, [(at(-30), 60, None), (at(15), 60, None)])
    add_trip(processor, bus[1] + swapped_schedule, D, [(at(-10), 0, None), (at(10), 0, None)])
    # An old id on the new day isn't in the new version: no mode, left out.
    add_trip(processor, bus[2], D, [(at(-10), 120, None), (at(10), 120, None)])
    bus_row = view_rows()["bus"]
    # bus[0] (60 s) and bus[1]:new (0 s). Reading only the active version would count bus[0]
    # and bus[2] instead: 2 trips with a delay, averaging 90 s.
    assert bus_row["running_trips"] == 2
    assert bus_row["trips_with_nonzero_delay"] == 1
    assert bus_row["avg_delay_s"] == 30.0


def test_apply_sql_is_idempotent() -> None:
    """Applying the views twice changes nothing and keeps btp_api's SELECT grant."""
    settings = MetricsJobsSettings()
    with connect("btp_metrics_jobs", "BTP_PG_METRICS_JOBS_PASSWORD", "dev_metrics_jobs") as conn:
        apply_sql(conn, view_files(settings.metrics_sql_dir))
        apply_sql(conn, view_files(settings.metrics_sql_dir))
    with connect("btp_api", "BTP_PG_API_PASSWORD", "dev_api") as conn:
        conn.execute("SELECT * FROM marts.v_now_by_mode").fetchall()
        assert conn.execute("SELECT latest_feed_ts FROM marts.v_feed_status").fetchone()
