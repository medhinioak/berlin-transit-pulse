"""Command line: btp-schedule import | refresh | activate | activate-due | status."""

import argparse
import json
import logging
import sys
import zipfile
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import psycopg

from btp_common.fixtures import mini_gtfs_dir
from btp_common.logging import configure_logging
from btp_schedule.backup import backup_zip
from btp_schedule.download import fetch
from btp_schedule.importer import (
    ImportFailed,
    ImportResult,
    NotStageableYet,
    activate,
    apply_schema,
    import_source,
    status,
)
from btp_schedule.lifecycle import berlin_today
from btp_schedule.refresh import activate_due, prune
from btp_schedule.settings import ScheduleSettings

log = logging.getLogger(__name__)


def _parser() -> argparse.ArgumentParser:
    """Build the argument parser."""
    parser = argparse.ArgumentParser(prog="btp-schedule", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    imp = commands.add_parser("import", help="import a static GTFS feed as a new version")
    ref = commands.add_parser(
        "refresh", help="import if changed, activate unless staged, back up the zip, prune"
    )
    for sub in (imp, ref):
        source = sub.add_mutually_exclusive_group()
        source.add_argument("--url", help="download from this URL (default: BTP_SCHEDULE_URL)")
        source.add_argument("--file", type=Path, help="a GTFS zip or folder on disk")
    imp.add_argument("--activate", action="store_true", help="activate the version when ready")
    act = commands.add_parser("activate", help="make a ready or retired version active")
    act.add_argument("version_id")
    commands.add_parser(
        "activate-due", help="activate a staged version starting tomorrow, or one overdue"
    )
    commands.add_parser("status", help="list versions and row counts")
    return parser


def _source(args: argparse.Namespace, settings: ScheduleSettings) -> tuple[Path, str]:
    """Return the feed to import and its source URL for the versions table."""
    if args.file is not None:
        return args.file, args.file.resolve().as_uri()
    if args.url is None and settings.feed_mode == "fixtures":
        path = mini_gtfs_dir()
        return path, path.resolve().as_uri()
    url = args.url or settings.schedule_url
    path = fetch(
        url, settings.schedule_cache_dir, settings.feed_contact, settings.schedule_max_age_hours
    )
    return path, url


def _print_status(conn: psycopg.Connection[object]) -> None:
    """Print one line per version."""
    for v in status(conn):
        counts = json.dumps(v["row_counts"]) if v["row_counts"] else ""
        dates = f"{v['feed_start_date']}..{v['feed_end_date']}" if v["feed_start_date"] else ""
        staged = f"activate_on={v['activate_on']}" if v["activate_on"] else ""
        print(f"{v['version_id']}  {v['status']:<9} {dates:<22} {staged:<22} {counts}")
        if v["failure_reason"]:
            print(f"    failed: {v['failure_reason']}")


def _backup(result: ImportResult, path: Path, settings: ScheduleSettings) -> None:
    """Copy an imported zip to the data bucket once, if one is set (folders are skipped).

    A marker in the cache directory records a done backup, so a failed one is retried on the
    next import or refresh of the same zip without uploading 84 MB every day.
    """
    if not settings.raw_bucket or not path.is_file():
        return
    marker = settings.schedule_cache_dir / "backed-up" / result.version_id
    if marker.exists():
        return
    if backup_zip(settings.raw_bucket, settings.aws_region, result.version_id, path):
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch()


def _refresh(
    conn: psycopg.Connection[object],
    args: argparse.Namespace,
    settings: ScheduleSettings,
    today: date,
) -> int:
    """Run the daily refresh; return 1 if download or import failed (the active version stays).

    The catch-up and the prune run either way.
    """
    code = 0
    try:
        path, url = _source(args, settings)
        result = import_source(conn, path, url, today=today, activate_after=True)
    except NotStageableYet:
        pass  # logged; not a failure: the next refresh in the staging window imports it
    except ImportFailed:
        code = 1
    except (httpx.HTTPError, OSError, zipfile.BadZipFile) as exc:
        log.error("download failed and no cached zip: %s", exc)
        code = 1
    else:
        _backup(result, path, settings)
    # A staged version whose start has passed (activate-due missed it) is caught up now.
    activate_due(conn, today, lead_days=0)
    prune(conn, today)
    return code


def main(argv: list[str] | None = None) -> int:
    """Run one command and return the exit code."""
    args = _parser().parse_args(argv)
    settings = ScheduleSettings()
    configure_logging(settings)
    with psycopg.connect(
        host=settings.pg_host,
        port=settings.pg_port,
        dbname=settings.pg_db,
        user=settings.pg_user,
        password=settings.pg_schedule_password,
        autocommit=True,
        connect_timeout=10,
    ) as conn:
        apply_schema(conn)
        today = berlin_today(datetime.now(UTC))
        if args.command == "import":
            path, url = _source(args, settings)
            try:
                result = import_source(conn, path, url, today=today, activate_after=args.activate)
            except NotStageableYet:
                return 0  # logged; the active version stays, and a deploy goes on quietly
            except ImportFailed:
                return 1
            _backup(result, path, settings)
        elif args.command == "activate":
            try:
                activate(conn, args.version_id)
            except ValueError as exc:
                log.error("%s", exc)
                return 1
        elif args.command == "refresh":
            return _refresh(conn, args, settings, today)
        elif args.command == "activate-due":
            activate_due(conn, today)
        else:
            _print_status(conn)
    return 0


if __name__ == "__main__":
    sys.exit(main())
