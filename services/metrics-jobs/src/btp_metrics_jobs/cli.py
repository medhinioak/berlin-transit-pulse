"""Command line: btp-metrics-jobs apply-sql."""

import argparse
import logging
import sys
import time
from pathlib import Path

import psycopg
from psycopg import errors
from pydantic import ValidationError

from btp_common.logging import configure_logging
from btp_metrics_jobs.settings import MetricsJobsSettings

log = logging.getLogger(__name__)

RETRY_INTERVAL_S = 2.0


def view_files(sql_dir: Path) -> list[Path]:
    """Return the .sql files in sql_dir/views, sorted by name (the order they are applied)."""
    files = sorted((sql_dir / "views").glob("*.sql"), key=lambda p: p.name)
    if not files:
        raise FileNotFoundError(f"no .sql files in {sql_dir / 'views'}")
    return files


def apply_sql(conn: psycopg.Connection[object], files: list[Path]) -> None:
    """Apply the files in order in one transaction: all of them or none."""
    with conn.transaction():
        for path in files:
            conn.execute(path.read_text(encoding="utf-8").encode())
            log.info("applied", extra={"fields": {"file": path.name}})


def apply_with_wait(
    settings: MetricsJobsSettings, files: list[Path], sleep: float = RETRY_INTERVAL_S
) -> None:
    """Apply the files, retrying while a table they read doesn't exist yet."""
    deadline = time.monotonic() + settings.wait_for_tables_s
    while True:
        try:
            with connect(settings) as conn:
                apply_sql(conn, files)
            return
        except (errors.UndefinedTable, errors.InvalidSchemaName) as exc:
            if time.monotonic() >= deadline:
                raise
            log.info("waiting for source tables", extra={"fields": {"error": str(exc).strip()}})
            time.sleep(sleep)


def connect(settings: MetricsJobsSettings) -> psycopg.Connection[object]:
    """Open an autocommit connection as the metrics-jobs role (transactions are explicit)."""
    return psycopg.connect(
        host=settings.pg_host,
        port=settings.pg_port,
        dbname=settings.pg_db,
        user=settings.pg_user,
        password=settings.pg_metrics_jobs_password,
        autocommit=True,
        connect_timeout=10,
    )


def _parser() -> argparse.ArgumentParser:
    """Build the argument parser."""
    parser = argparse.ArgumentParser(prog="btp-metrics-jobs", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("apply-sql", help="create or replace the marts views in sql/views/")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run a command; return the exit code (1 on a database error, 2 on bad settings)."""
    args = _parser().parse_args(argv)
    try:
        settings = MetricsJobsSettings()
    except ValidationError as exc:
        print(f"btp-metrics-jobs: invalid settings:\n{exc}", file=sys.stderr)
        return 2
    configure_logging(settings)
    if args.command == "apply-sql":
        try:
            files = view_files(settings.metrics_sql_dir)
            apply_with_wait(settings, files)
        except FileNotFoundError as exc:
            log.error("%s", exc)
            return 2
        except psycopg.Error as exc:
            log.error("apply-sql failed", extra={"fields": {"error": str(exc).strip()}})
            return 1
        log.info("apply-sql done", extra={"fields": {"files": len(files)}})
    return 0


if __name__ == "__main__":
    sys.exit(main())
