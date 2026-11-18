"""Integration tests: import, re-import, a second version, activation and a failed import.

Needs `make up`. Runs as btp_schedule against the compose Postgres (override with BTP_PG_HOST,
BTP_PG_PORT, BTP_PG_SCHEDULE_PASSWORD). The version that was active before is restored.
"""

import os
import zipfile
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import psycopg
import pytest
from psycopg import sql

from btp_common.fixtures import mini_gtfs_dir
from btp_schedule.importer import ImportFailed, activate, apply_schema, import_source

pytestmark = pytest.mark.integration

TODAY = date(2026, 11, 20)
TABLES = (
    "agency",
    "routes",
    "trips",
    "stops",
    "stop_times",
    "calendar",
    "calendar_dates",
    "feed_info",
)


def _zip(path: Path, replace: dict[str, str] | None = None, drop: str | None = None) -> Path:
    """Zip the mini GTFS deterministically, optionally replacing or dropping files."""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for file in sorted(mini_gtfs_dir().glob("*.txt")):
            if file.name == drop:
                continue
            data = (replace or {}).get(file.name, file.read_text(encoding="utf-8"))
            z.writestr(zipfile.ZipInfo(file.name, date_time=(2026, 1, 1, 0, 0, 0)), data)
    return path


def _status(conn: psycopg.Connection[Any], version_id: str) -> str | None:
    """Return the status of a version, or None if it doesn't exist."""
    row = conn.execute(
        "SELECT status FROM schedule.versions WHERE version_id = %s", (version_id,)
    ).fetchone()
    return None if row is None else str(row[0])


def _active(conn: psycopg.Connection[Any]) -> str | None:
    """Return the active version id."""
    row = conn.execute(
        "SELECT version_id FROM schedule.versions WHERE status = 'active'"
    ).fetchone()
    return None if row is None else str(row[0])


def _count(conn: psycopg.Connection[Any], view: str) -> int:
    """Count the rows of one schedule view."""
    query = sql.SQL("SELECT count(*) FROM schedule.{}").format(sql.Identifier(view))
    row = conn.execute(query).fetchone()
    assert row is not None
    return int(row[0])


@pytest.fixture
def conn() -> Iterator[psycopg.Connection[Any]]:
    """Connect as btp_schedule; afterwards delete the versions the test made, restore the active."""
    with psycopg.connect(
        host=os.environ.get("BTP_PG_HOST", "localhost"),
        port=int(os.environ.get("BTP_PG_PORT", "5432")),
        dbname="btp",
        user="btp_schedule",
        password=os.environ.get("BTP_PG_SCHEDULE_PASSWORD", "dev_schedule"),
        autocommit=True,
        connect_timeout=5,
    ) as conn:
        apply_schema(conn)
        before = {r[0] for r in conn.execute("SELECT version_id FROM schedule.versions")}
        previously_active = _active(conn)
        yield conn
        created = [
            r[0]
            for r in conn.execute("SELECT version_id FROM schedule.versions")
            if r[0] not in before
        ]
        if previously_active:
            activate(conn, previously_active)
        with conn.transaction():
            for table in (*TABLES, "versions"):
                conn.execute(
                    sql.SQL("DELETE FROM schedule.{} WHERE version_id = ANY(%s)").format(
                        sql.Identifier(table)
                    ),
                    (created,),
                )


def test_import_reimport_and_switch(conn: psycopg.Connection[Any], tmp_path: Path) -> None:
    """Same zip is a no-op; a changed zip is a new version; activation switches the views."""
    first_zip = _zip(tmp_path / "first.zip")
    first = import_source(conn, first_zip, "test:first", today=TODAY, activate_after=True)
    assert first.imported
    assert _status(conn, first.version_id) == "active"
    assert _count(conn, "v_routes") == 167
    assert _count(conn, "v_stop_times") == 7497
    row = conn.execute(
        "SELECT count(*) FILTER (WHERE mode IS NULL), count(*) FILTER (WHERE in_berlin) "
        "FROM schedule.v_routes"
    ).fetchone()
    assert row is not None and row[0] == 0 and row[1] > 0

    versions = _count(conn, "versions")
    again = import_source(conn, first_zip, "test:first", today=TODAY, activate_after=True)
    assert again.version_id == first.version_id
    assert not again.imported
    assert _count(conn, "versions") == versions

    agency = (mini_gtfs_dir() / "agency.txt").read_text(encoding="utf-8")
    changed = agency.replace("S-Bahn Berlin GmbH", "S-Bahn Berlin GmbH (test)")
    second_zip = _zip(tmp_path / "second.zip", replace={"agency.txt": changed})
    second = import_source(conn, second_zip, "test:second", today=TODAY)
    assert second.imported and second.version_id != first.version_id
    assert _status(conn, second.version_id) == "ready"
    assert _active(conn) == first.version_id  # ready, not yet active

    activate(conn, second.version_id)
    assert _status(conn, second.version_id) == "active"
    assert _status(conn, first.version_id) == "retired"
    assert _count(conn, "v_routes") == 167  # one version's rows, never both
    names = {r[0] for r in conn.execute("SELECT agency_name FROM schedule.v_agency")}
    assert "S-Bahn Berlin GmbH (test)" in names
    assert "S-Bahn Berlin GmbH" not in names

    activate(conn, first.version_id)  # a retired version can be re-activated (rollback)
    assert _active(conn) == first.version_id


def test_failed_import_leaves_active_version(conn: psycopg.Connection[Any], tmp_path: Path) -> None:
    """A version that fails validation is marked failed and the views don't change."""
    good = import_source(
        conn, _zip(tmp_path / "good.zip"), "test:good", today=TODAY, activate_after=True
    )
    routes = _count(conn, "v_routes")

    bad_zip = _zip(tmp_path / "bad.zip", drop="stop_times.txt")
    with pytest.raises(ImportFailed, match="missing file stop_times.txt"):
        import_source(conn, bad_zip, "test:bad", today=TODAY, activate_after=True)
    bad_id = conn.execute(
        "SELECT version_id FROM schedule.versions WHERE source_url = 'test:bad'"
    ).fetchone()
    assert bad_id is not None
    assert _status(conn, bad_id[0]) == "failed"
    assert _active(conn) == good.version_id
    assert _count(conn, "v_routes") == routes

    with pytest.raises(ImportFailed, match="don't cover"):
        import_source(
            conn,
            _zip(tmp_path / "late.zip", replace={"agency.txt": "agency_id\nx\n"}),
            "test:late",
            today=date(2030, 1, 1),
        )
    assert _active(conn) == good.version_id
    with pytest.raises(ValueError, match="failed"):
        activate(conn, bad_id[0])
