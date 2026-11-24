"""Scheduled jobs on the versions: activate a staged version when due, prune old versions.

Like importer, every function expects a connection in autocommit mode.
"""

import logging
import time
from datetime import date, datetime

from psycopg import sql

from btp_schedule.importer import IMPORT_LOCK, Connection, activate
from btp_schedule.lifecycle import Staged, VersionState, due_version, prune_candidates

log = logging.getLogger(__name__)

# Tables holding a version's rows, largest first; versions itself goes last.
VERSION_TABLES = (
    "stop_times",
    "trips",
    "stops",
    "routes",
    "calendar_dates",
    "calendar",
    "agency",
    "feed_info",
)
DELETE_BATCH = 50_000


def activate_due(
    conn: Connection, today: date, *, lead_days: int = 1, at: datetime | None = None
) -> str | None:
    """Activate the staged version that is due (lifecycle.due_version); return its id, or None."""
    rows = conn.execute(
        "SELECT version_id, activate_on FROM schedule.versions "
        "WHERE status = 'ready' AND activate_on IS NOT NULL"
    ).fetchall()
    version_id = due_version((Staged(r[0], r[1]) for r in rows), today, lead_days)
    if version_id is None:
        log.info("no staged version is due (%d staged)", len(rows))
        return None
    activate(conn, version_id, at=at)
    return version_id


def prune(conn: Connection) -> list[str]:
    """Delete the versions prune_candidates selects, in batches, then vacuum; return their ids.

    Holds the import lock, so no import runs meanwhile. Each batch commits on its own: an
    interrupted prune leaves a version partly deleted, and the next prune finishes it.
    """
    conn.execute("SELECT pg_advisory_lock(%s)", (IMPORT_LOCK,))
    try:
        rows = conn.execute(
            """
            SELECT v.version_id, v.status, v.created_at, max(a.deactivated_at), now()
            FROM schedule.versions v
            LEFT JOIN schedule.version_activations a USING (version_id)
            GROUP BY v.version_id
            """
        ).fetchall()
        if not rows:
            return []
        now = rows[0][4]
        doomed = prune_candidates((VersionState(r[0], r[1], r[2], r[3]) for r in rows), now)
        for version_id in doomed:
            started = time.monotonic()
            deleted = {
                table: _delete_version_rows(conn, table, version_id) for table in VERSION_TABLES
            }
            conn.execute("DELETE FROM schedule.versions WHERE version_id = %s", (version_id,))
            log.info(
                "pruned version %s in %.1f s: %s",
                version_id,
                time.monotonic() - started,
                deleted,
            )
    finally:
        if not conn.broken:
            conn.execute("SELECT pg_advisory_unlock(%s)", (IMPORT_LOCK,))
    if doomed:
        started = time.monotonic()
        for table in (*VERSION_TABLES, "versions"):
            # Not FULL: the freed space is reused by the next import, without an exclusive lock.
            conn.execute(sql.SQL("VACUUM (ANALYZE) schedule.{}").format(sql.Identifier(table)))
        log.info("vacuumed the schedule tables in %.1f s", time.monotonic() - started)
    return doomed


def _delete_version_rows(conn: Connection, table: str, version_id: str) -> int:
    """Delete one version's rows from table in committed batches; return how many."""
    statement = sql.SQL(
        "DELETE FROM schedule.{t} WHERE ctid IN "
        "(SELECT ctid FROM schedule.{t} WHERE version_id = %s LIMIT %s)"
    ).format(t=sql.Identifier(table))
    total = 0
    while True:
        cur = conn.execute(statement, (version_id, DELETE_BATCH))
        total += cur.rowcount
        if cur.rowcount < DELETE_BATCH:
            return total
