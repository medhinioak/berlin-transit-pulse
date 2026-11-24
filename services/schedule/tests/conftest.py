"""Fixtures for the schedule integration tests: a connection that undoes what a test did.

Needs `make up`. Runs as btp_schedule against the compose Postgres (override with BTP_PG_HOST,
BTP_PG_PORT, BTP_PG_SCHEDULE_PASSWORD).
"""

import os
import zipfile
from collections.abc import Callable, Iterator
from datetime import date
from pathlib import Path
from typing import Any

import psycopg
import pytest
from psycopg import sql

from btp_common.fixtures import mini_gtfs_dir
from btp_schedule.importer import apply_schema

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

ZipMaker = Callable[..., Path]


def connect() -> psycopg.Connection[Any]:
    """Connect as btp_schedule in autocommit mode."""
    return psycopg.connect(
        host=os.environ.get("BTP_PG_HOST", "localhost"),
        port=int(os.environ.get("BTP_PG_PORT", "5432")),
        dbname="btp",
        user="btp_schedule",
        password=os.environ.get("BTP_PG_SCHEDULE_PASSWORD", "dev_schedule"),
        autocommit=True,
        connect_timeout=5,
    )


@pytest.fixture
def conn() -> Iterator[psycopg.Connection[Any]]:
    """Connect as btp_schedule; afterwards restore versions and their activation history.

    Versions the test created are deleted. Versions that existed get their status and
    activation time back, and version_activations is restored row by row, so simulated
    activations (with made-up times) don't leak into the dev stack.
    """
    with connect() as conn:
        apply_schema(conn)
        versions = conn.execute(
            "SELECT version_id, status, activated_at FROM schedule.versions"
        ).fetchall()
        activations = conn.execute(
            "SELECT activation_id, version_id, activated_at, effective_from, deactivated_at "
            "FROM schedule.version_activations"
        ).fetchall()
        yield conn
        before = [v[0] for v in versions]
        with conn.transaction():
            for table in (*TABLES, "versions"):
                conn.execute(
                    sql.SQL("DELETE FROM schedule.{} WHERE NOT version_id = ANY(%s)").format(
                        sql.Identifier(table)
                    ),
                    (before,),
                )
            # Free the one-active slot first, then put every status back.
            conn.execute("UPDATE schedule.versions SET status = 'retired' WHERE status = 'active'")
            for version_id, status, activated_at in sorted(
                versions, key=lambda v: v[1] == "active"
            ):
                conn.execute(
                    "UPDATE schedule.versions SET status = %s, activated_at = %s "
                    "WHERE version_id = %s",
                    (status, activated_at, version_id),
                )
            conn.execute("DELETE FROM schedule.version_activations")
            for row in activations:
                conn.execute(
                    "INSERT INTO schedule.version_activations (activation_id, version_id, "
                    "activated_at, effective_from, deactivated_at) OVERRIDING SYSTEM VALUE "
                    "VALUES (%s, %s, %s, %s, %s)",
                    row,
                )


def _mini_zip(path: Path, replace: dict[str, str] | None = None, drop: str | None = None) -> Path:
    """Zip the mini GTFS deterministically, optionally replacing or dropping files."""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for file in sorted(mini_gtfs_dir().glob("*.txt")):
            if file.name == drop:
                continue
            data = (replace or {}).get(file.name, file.read_text(encoding="utf-8"))
            z.writestr(zipfile.ZipInfo(file.name, date_time=(2026, 1, 1, 0, 0, 0)), data)
    return path


@pytest.fixture
def make_zip() -> ZipMaker:
    """Return _mini_zip: zip the mini GTFS, replacing (name -> text) or dropping files."""
    return _mini_zip


def _calendar_between(start: date, end: date) -> dict[str, str]:
    """Return replacements giving the mini GTFS the date range start..end.

    Every calendar row gets these dates and calendar_dates is emptied, so the feed's range
    (taken from calendar and calendar_dates; the mini GTFS has no feed_info) is exactly that.
    """
    lines = (mini_gtfs_dir() / "calendar.txt").read_text(encoding="utf-8").splitlines()
    header, rows = lines[0], [line.split(",") for line in lines[1:] if line]
    dates = [f"{start:%Y%m%d}", f"{end:%Y%m%d}"]
    calendar = "\n".join([header, *(",".join(r[:8] + dates) for r in rows)]) + "\n"
    return {"calendar.txt": calendar, "calendar_dates.txt": "service_id,date,exception_type\n"}


@pytest.fixture
def calendar_between() -> Callable[[date, date], dict[str, str]]:
    """Return _calendar_between: replacements that give the mini GTFS a date range."""
    return _calendar_between


@pytest.fixture
def new_connection() -> Callable[[], psycopg.Connection[Any]]:
    """Return connect, for tests that need a second connection."""
    return connect
