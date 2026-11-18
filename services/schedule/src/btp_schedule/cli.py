"""Command line: btp-schedule import | activate | status."""

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg

from btp_common.fixtures import mini_gtfs_dir
from btp_common.logging import configure_logging
from btp_schedule.download import fetch
from btp_schedule.importer import ImportFailed, activate, apply_schema, import_source, status
from btp_schedule.settings import ScheduleSettings

log = logging.getLogger(__name__)


def _parser() -> argparse.ArgumentParser:
    """Build the argument parser."""
    parser = argparse.ArgumentParser(prog="btp-schedule", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    imp = commands.add_parser("import", help="import a static GTFS feed as a new version")
    source = imp.add_mutually_exclusive_group()
    source.add_argument("--url", help="download from this URL (default: BTP_SCHEDULE_URL)")
    source.add_argument("--file", type=Path, help="a GTFS zip or folder on disk")
    imp.add_argument("--activate", action="store_true", help="activate the version when ready")
    act = commands.add_parser("activate", help="make a ready or retired version active")
    act.add_argument("version_id")
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
        print(f"{v['version_id']}  {v['status']:<9} {dates:<22} {counts}")
        if v["failure_reason"]:
            print(f"    failed: {v['failure_reason']}")


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
        if args.command == "import":
            path, url = _source(args, settings)
            today = datetime.now(ZoneInfo("Europe/Berlin")).date()
            try:
                import_source(conn, path, url, today=today, activate_after=args.activate)
            except ImportFailed:
                return 1
        elif args.command == "activate":
            try:
                activate(conn, args.version_id)
            except ValueError as exc:
                log.error("%s", exc)
                return 1
        else:
            _print_status(conn)
    return 0


if __name__ == "__main__":
    sys.exit(main())
