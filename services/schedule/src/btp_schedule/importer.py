"""Import a static GTFS feed into schema schedule as a version, validate it and activate it.

All functions expect a connection in autocommit mode and open their own transactions.
"""

import csv
import json
import logging
import time
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from importlib.resources import files
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from btp_schedule.gtfs import (
    COLUMNS,
    DERIVED,
    LOAD_ORDER,
    REQUIRED_FILES,
    GtfsSource,
    Row,
    feed_start_date,
    plain_rows,
    route_rows,
    stop_time_rows,
    trip_rows,
)
from btp_schedule.lifecycle import (
    Staged,
    activate_on_for,
    berlin_today,
    date_range_errors,
    effective_from,
    superseded,
    too_far_ahead,
)

log = logging.getLogger(__name__)

Connection = psycopg.Connection[Any]

# Tables that must have rows for a version to be usable.
NON_EMPTY_TABLES = ("routes", "trips", "stops", "stop_times")
# Statuses whose version is complete; importing the same zip again is a no-op.
COMPLETE = ("ready", "active", "retired")
# Session advisory lock held for a whole import, so two imports never run at once.
IMPORT_LOCK = 0x5C4ED01E


class ImportFailed(Exception):
    """The import or its validation failed; the version is marked failed."""


class NotStageableYet(ImportFailed):
    """The feed starts too far ahead to be staged; nothing was loaded or recorded."""


@dataclass(frozen=True)
class ImportResult:
    """Outcome of an import: the version and whether it was imported now."""

    version_id: str
    imported: bool


def apply_schema(conn: Connection) -> None:
    """Create the schedule tables and views if they don't exist."""
    for resource in sorted(files("btp_schedule").joinpath("sql").iterdir(), key=lambda r: r.name):
        if resource.name.endswith(".sql"):
            conn.execute(resource.read_text(encoding="utf-8").encode())


def validation_errors(
    row_counts: dict[str, int],
    unknown_trip_ids: list[str],
    date_range: tuple[date | None, date | None],
    today: date,
) -> list[str]:
    """Return the reasons a loaded version must not be used; empty if it is fine.

    The feed must not have ended, and may start up to STAGING_DAYS after today (staging).
    """
    errors = [f"{t} has no rows" for t in NON_EMPTY_TABLES if row_counts.get(t, 0) == 0]
    if unknown_trip_ids:
        errors.append(f"stop_times reference unknown trips, e.g. {', '.join(unknown_trip_ids)}")
    start, end = date_range
    if start is None or end is None:
        errors.append("no feed date range (feed_info, calendar and calendar_dates are empty)")
    else:
        errors += date_range_errors(start, end, today)
    return errors


def _copy(conn: Connection, table: str, rows: Iterable[Row]) -> int:
    """COPY rows into schedule.<table> and return how many were written."""
    columns = ("version_id", *COLUMNS[table], *DERIVED.get(table, ()))
    statement = sql.SQL("COPY schedule.{} ({}) FROM STDIN").format(
        sql.Identifier(table), sql.SQL(", ").join(map(sql.Identifier, columns))
    )
    count = 0
    with conn.cursor() as cur, cur.copy(statement) as copy:
        for row in rows:
            copy.write_row(row)
            count += 1
    return count


def _load(conn: Connection, source: GtfsSource, names: set[str], version_id: str) -> dict[str, int]:
    """Stream every file into its table and return the row counts. Call inside a transaction."""
    counts: dict[str, int] = {}
    berlin_trips: set[str] = set()
    berlin_routes: set[str] = set()
    for table in LOAD_ORDER:
        if f"{table}.txt" not in names:
            continue
        started = time.monotonic()
        rows: Iterable[Row]
        if table == "stop_times":
            rows = stop_time_rows(source, version_id, berlin_trips)
        elif table == "trips":
            rows = trip_rows(source, version_id, berlin_trips, berlin_routes)
        elif table == "routes":
            rows = route_rows(source, version_id, berlin_routes)
        else:
            rows = plain_rows(source, table, version_id)
        counts[table] = _copy(conn, table, rows)
        log.info("loaded %s: %d rows in %.1f s", table, counts[table], time.monotonic() - started)
        # Without fresh statistics the planner assumes a new version has ~1 row and picks a
        # nested loop for the validation anti-join: minutes instead of seconds on real data.
        conn.execute(sql.SQL("ANALYZE schedule.{}").format(sql.Identifier(table)))
    return counts


def _unknown_trip_ids(conn: Connection, version_id: str) -> list[str]:
    """Return up to 5 trip_ids used in stop_times that aren't in trips."""
    rows = conn.execute(
        """
        SELECT DISTINCT st.trip_id FROM schedule.stop_times st
        WHERE st.version_id = %(v)s AND NOT EXISTS (
            SELECT 1 FROM schedule.trips t
            WHERE t.version_id = st.version_id AND t.trip_id = st.trip_id)
        LIMIT 5
        """,
        {"v": version_id},
    ).fetchall()
    return [r[0] for r in rows]


def _date_range(conn: Connection, version_id: str) -> tuple[date | None, date | None]:
    """Return the feed's date range: feed_info if set, else calendar and calendar_dates."""
    row = conn.execute(
        """
        SELECT coalesce(fi.s, c.s), coalesce(fi.e, c.e)
        FROM (SELECT min(feed_start_date) AS s, max(feed_end_date) AS e
              FROM schedule.feed_info WHERE version_id = %(v)s) fi,
             (SELECT min(d) AS s, max(d) AS e FROM (
                  SELECT start_date AS d FROM schedule.calendar WHERE version_id = %(v)s
                  UNION ALL SELECT end_date FROM schedule.calendar WHERE version_id = %(v)s
                  UNION ALL SELECT date FROM schedule.calendar_dates WHERE version_id = %(v)s
              ) dates) c
        """,
        {"v": version_id},
    ).fetchone()
    return (row[0], row[1]) if row else (None, None)


def _downloaded_at(path: Path) -> datetime | None:
    """Return when a zip was downloaded (its modification time); None for a folder."""
    if path.is_dir():
        return None
    return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)


def import_source(
    conn: Connection, path: Path, source_url: str, *, today: date, activate_after: bool = False
) -> ImportResult:
    """Import the feed at path as a new version, unless a complete version of it exists.

    Raises ImportFailed if loading or validation fails; the version is then marked failed and
    the active version is untouched. With activate_after, a new or ready version is activated;
    a retired one is left alone, so a manual rollback survives the next `make up`.
    """
    source = GtfsSource(path)
    sha256 = source.sha256()
    # A second import (e.g. compose and a manual run) waits here, then finds the version done.
    conn.execute("SELECT pg_advisory_lock(%s)", (IMPORT_LOCK,))
    try:
        return _import_locked(conn, source, path, sha256, source_url, today, activate_after)
    finally:
        if not conn.broken:  # a lost connection has released the lock already
            conn.execute("SELECT pg_advisory_unlock(%s)", (IMPORT_LOCK,))


def _import_locked(
    conn: Connection,
    source: GtfsSource,
    path: Path,
    sha256: str,
    source_url: str,
    today: date,
    activate_after: bool,
) -> ImportResult:
    """Run import_source while holding the import lock."""
    version_id = sha256[:12]
    row = conn.execute(
        "SELECT status, activate_on FROM schedule.versions WHERE version_id = %s", (version_id,)
    ).fetchone()
    if row is not None and row[0] in COMPLETE:
        log.info("version %s already imported (%s), nothing to do", version_id, row[0])
        if activate_after and row[0] == "ready":
            _activate_unless_staged(conn, version_id, row[1], today)
        elif activate_after and row[0] == "retired":
            log.warning(
                "version %s is retired, not re-activating it; use `btp-schedule activate`",
                version_id,
            )
        return ImportResult(version_id, imported=False)

    # Checked before loading: a feed published weeks ahead would otherwise be loaded and
    # rolled back on every daily refresh until its staging window opens.
    start = feed_start_date(source, source.names())
    if start is not None and (reason := too_far_ahead(start, today)):
        log.warning("version %s not imported: %s", version_id, reason)
        raise NotStageableYet(reason)

    # A failed or interrupted earlier attempt is retried. Its rows were never committed: data
    # and the ready status are written in one transaction.
    with conn.transaction():
        conn.execute(
            """
            INSERT INTO schedule.versions (version_id, sha256, source_url, downloaded_at, status)
            VALUES (%(v)s, %(sha)s, %(url)s, %(at)s, 'importing')
            ON CONFLICT (version_id) DO UPDATE SET
                status = 'importing', failure_reason = NULL, source_url = EXCLUDED.source_url,
                downloaded_at = EXCLUDED.downloaded_at, created_at = now()
            """,
            {"v": version_id, "sha": sha256, "url": source_url, "at": _downloaded_at(path)},
        )
    log.info("importing version %s from %s", version_id, source_url)
    started = time.monotonic()
    try:
        with conn.transaction():
            names = source.names()
            missing = [name for name in REQUIRED_FILES if name not in names]
            if missing:
                raise ImportFailed("; ".join(f"missing file {name}" for name in missing))
            counts = _load(conn, source, names, version_id)
            date_range = _date_range(conn, version_id)
            errors = validation_errors(
                counts, _unknown_trip_ids(conn, version_id), date_range, today
            )
            if errors:
                raise ImportFailed("; ".join(errors))
            activate_on = activate_on_for(date_range[0], today)
            conn.execute(
                """
                UPDATE schedule.versions SET status = 'ready', row_counts = %(counts)s,
                    feed_start_date = %(start)s, feed_end_date = %(end)s,
                    activate_on = %(activate_on)s
                WHERE version_id = %(v)s
                """,
                {
                    "v": version_id,
                    "counts": json.dumps(counts),
                    "start": date_range[0],
                    "end": date_range[1],
                    "activate_on": activate_on,
                },
            )
    except (ImportFailed, psycopg.Error, ValueError, OSError, csv.Error, zipfile.BadZipFile) as exc:
        reason = str(exc) or type(exc).__name__
        log.error("version %s failed: %s", version_id, reason)
        try:
            with conn.transaction():
                # Only our own attempt: never overwrite a version that is ready or active.
                conn.execute(
                    "UPDATE schedule.versions SET status = 'failed', failure_reason = %s "
                    "WHERE version_id = %s AND status = 'importing'",
                    (reason, version_id),
                )
        except psycopg.Error as record_exc:
            # E.g. the connection is gone. The row stays 'importing' and is retried next time.
            log.error("could not record the failure of %s: %s", version_id, record_exc)
        raise ImportFailed(reason) from exc
    log.info("version %s ready in %.1f s", version_id, time.monotonic() - started)
    if activate_after:
        _activate_unless_staged(conn, version_id, activate_on, today)
    return ImportResult(version_id, imported=True)


def _activate_unless_staged(
    conn: Connection, version_id: str, activate_on: date | None, today: date
) -> None:
    """Activate a ready version now, unless it is staged for a later day (activate-due does)."""
    if activate_on is not None and activate_on > today:
        log.info("version %s is staged: activate-due activates it for %s", version_id, activate_on)
        return
    activate(conn, version_id)


def activate(conn: Connection, version_id: str, *, at: datetime | None = None) -> None:
    """Make version_id the active version and retire the previous one, in one transaction.

    Records the activation in version_activations (closing the previous one), with the time it
    starts to apply to service days (lifecycle.effective_from), and clears activate_on of the
    staged versions it supersedes (lifecycle.superseded). at defaults to the database's now();
    tests pass it to simulate a swap night.
    """
    with conn.transaction():
        # Serialises activations; readers of the views are not blocked.
        conn.execute("LOCK TABLE schedule.versions IN SHARE ROW EXCLUSIVE MODE")
        row = conn.execute(
            """
            SELECT status, activate_on, now(),
                NOT EXISTS (SELECT 1 FROM schedule.version_activations a
                            WHERE a.version_id = v.version_id)
            FROM schedule.versions v WHERE version_id = %s
            """,
            (version_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"unknown version {version_id}")
        current, activate_on, db_now, first_activation = row
        if current == "active":
            log.info("version %s is already active", version_id)
            return
        if current not in ("ready", "retired"):
            raise ValueError(f"version {version_id} is {current}, not ready or retired")
        at = at or db_now
        latest = conn.execute(
            "SELECT max(effective_from) FROM schedule.version_activations"
        ).fetchone()
        starts = effective_from(at, activate_on, first_activation, latest[0] if latest else None)
        staged = [
            Staged(r[0], r[1], r[2])
            for r in conn.execute(
                "SELECT version_id, activate_on, created_at FROM schedule.versions "
                "WHERE status = 'ready' AND activate_on IS NOT NULL AND version_id <> %s",
                (version_id,),
            )
        ]
        obsolete = superseded(staged, activate_on, berlin_today(at))
        conn.execute("UPDATE schedule.versions SET status = 'retired' WHERE status = 'active'")
        conn.execute(
            "UPDATE schedule.versions SET status = 'active', activated_at = %s "
            "WHERE version_id = %s",
            (at, version_id),
        )
        conn.execute(
            "UPDATE schedule.version_activations SET deactivated_at = %s "
            "WHERE deactivated_at IS NULL",
            (at,),
        )
        conn.execute(
            "INSERT INTO schedule.version_activations (version_id, activated_at, effective_from) "
            "VALUES (%s, %s, %s)",
            (version_id, at, starts),
        )
        if obsolete:
            conn.execute(
                "UPDATE schedule.versions SET activate_on = NULL WHERE version_id = ANY(%s)",
                (obsolete,),
            )
            log.info("superseded staged versions %s; activate-due skips them now", obsolete)
    log.info("version %s is now active", version_id)


def status(conn: Connection) -> list[dict[str, Any]]:
    """Return every version, newest first, with its status, dates and row counts."""
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(
            """
            SELECT version_id, status, created_at, activated_at, feed_start_date,
                   feed_end_date, activate_on, row_counts, failure_reason, source_url
            FROM schedule.versions ORDER BY created_at DESC
            """
        ).fetchall()
