"""Integration tests: import, re-import, a second version, activation and a failed import.

Needs `make up`; the fixtures are in conftest.py. The versions as they were before are restored.
"""

import threading
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

import psycopg
import pytest
from psycopg import sql

from btp_common.fixtures import mini_gtfs_dir
from btp_schedule.importer import (
    ImportFailed,
    ImportResult,
    activate,
    import_source,
)

pytestmark = pytest.mark.integration

TODAY = date(2026, 11, 20)


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


def test_import_reimport_and_switch(
    conn: psycopg.Connection[Any], make_zip: Callable[..., Path], tmp_path: Path
) -> None:
    """Same zip is a no-op; a changed zip is a new version; activation switches the views."""
    first_zip = make_zip(tmp_path / "first.zip")
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
    second_zip = make_zip(tmp_path / "second.zip", replace={"agency.txt": changed})
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

    # The next `import --activate` of the newer zip must not undo that rollback.
    again = import_source(conn, second_zip, "test:second", today=TODAY, activate_after=True)
    assert not again.imported
    assert _status(conn, second.version_id) == "retired"
    assert _active(conn) == first.version_id


def test_concurrent_imports_of_same_zip(
    conn: psycopg.Connection[Any],
    make_zip: Callable[..., Path],
    new_connection: Callable[[], psycopg.Connection[Any]],
    tmp_path: Path,
) -> None:
    """Two imports at once: one imports and activates, the other waits and finds it done."""
    zip_path = make_zip(
        tmp_path / "same.zip",
        replace={"agency.txt": "agency_id,agency_name\n1,concurrent\n"},
    )
    results: list[ImportResult | Exception] = []

    def run() -> None:
        with new_connection() as other:
            try:
                results.append(
                    import_source(other, zip_path, "test:same", today=TODAY, activate_after=True)
                )
            except Exception as exc:  # reported by the assertions below
                results.append(exc)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    ok = [r for r in results if isinstance(r, ImportResult)]
    assert len(ok) == 2, results
    assert sorted(r.imported for r in ok) == [False, True]
    assert ok[0].version_id == ok[1].version_id
    assert _status(conn, ok[0].version_id) == "active"


def test_failed_import_leaves_active_version(
    conn: psycopg.Connection[Any], make_zip: Callable[..., Path], tmp_path: Path
) -> None:
    """A version that fails validation is marked failed and the views don't change."""
    good = import_source(
        conn, make_zip(tmp_path / "good.zip"), "test:good", today=TODAY, activate_after=True
    )
    routes = _count(conn, "v_routes")

    bad_zip = make_zip(tmp_path / "bad.zip", drop="stop_times.txt")
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
            make_zip(tmp_path / "late.zip", replace={"agency.txt": "agency_id\nx\n"}),
            "test:late",
            today=date(2030, 1, 1),
        )
    assert _active(conn) == good.version_id
    with pytest.raises(ValueError, match="failed"):
        activate(conn, bad_id[0])


@pytest.mark.parametrize("today", [date(2026, 12, 13), date(2026, 12, 14), date(2027, 6, 1)])
def test_mini_gtfs_imports_after_timetable_change(
    conn: psycopg.Connection[Any], make_zip: Callable[..., Path], tmp_path: Path, today: date
) -> None:
    """Fixtures mode keeps importing after VBB's 2026-12-13 timetable change (#75).

    The mini GTFS carries its own dates (to 2027-12-11), so the real calendar doesn't matter.
    """
    agency = (mini_gtfs_dir() / "agency.txt").read_text(encoding="utf-8")
    changed = agency.replace("S-Bahn Berlin GmbH", f"S-Bahn Berlin GmbH ({today})")
    result = import_source(
        conn,
        make_zip(tmp_path / "mini.zip", replace={"agency.txt": changed}),
        "test:mini",
        today=today,
    )
    assert result.imported
    assert _status(conn, result.version_id) == "ready"
