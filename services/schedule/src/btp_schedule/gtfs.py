"""Read a static GTFS feed (zip or folder) and turn its rows into rows for the schedule tables."""

import csv
import hashlib
import io
import re
import zipfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import TextIO

from btp_schedule.modes import mode_for_route_type

# Berlin's state code in the German stop-ID scheme (DHID): de:11000:900007104::2.
BERLIN_STOP_PREFIX = "de:11000:"

# The source columns loaded into each table, in table order after version_id. A column missing
# from a file becomes NULL. Derived columns (see the *_rows functions) come after these.
COLUMNS: dict[str, tuple[str, ...]] = {
    "agency": (
        "agency_id",
        "agency_name",
        "agency_url",
        "agency_timezone",
        "agency_lang",
        "agency_phone",
    ),
    "routes": (
        "route_id",
        "agency_id",
        "route_short_name",
        "route_long_name",
        "route_type",
        "route_color",
        "route_text_color",
        "route_desc",
    ),
    "trips": (
        "route_id",
        "service_id",
        "trip_id",
        "trip_headsign",
        "trip_short_name",
        "direction_id",
        "block_id",
        "shape_id",
        "wheelchair_accessible",
        "bikes_allowed",
    ),
    "stops": (
        "stop_id",
        "stop_code",
        "stop_name",
        "stop_desc",
        "stop_lat",
        "stop_lon",
        "location_type",
        "parent_station",
        "wheelchair_boarding",
        "platform_code",
        "zone_id",
        "level_id",
    ),
    "stop_times": (
        "trip_id",
        "stop_id",
        "stop_sequence",
        "arrival_time",
        "departure_time",
        "pickup_type",
        "drop_off_type",
        "stop_headsign",
    ),
    "calendar": (
        "service_id",
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
        "start_date",
        "end_date",
    ),
    "calendar_dates": ("service_id", "date", "exception_type"),
    "feed_info": (
        "feed_publisher_name",
        "feed_publisher_url",
        "feed_lang",
        "feed_start_date",
        "feed_end_date",
        "feed_version",
    ),
}
DERIVED: dict[str, tuple[str, ...]] = {
    "routes": ("mode", "in_berlin"),
    "trips": ("in_berlin",),
    "stop_times": ("arrival_secs", "departure_secs"),
}
# in_berlin flows from stop_times to trips to routes, so they load in this order.
LOAD_ORDER = (
    "stops",
    "stop_times",
    "trips",
    "routes",
    "agency",
    "calendar",
    "calendar_dates",
    "feed_info",
)
REQUIRED_FILES = ("agency.txt", "routes.txt", "trips.txt", "stops.txt", "stop_times.txt")

Row = tuple[str | int | bool | None, ...]

_TIME = re.compile(r"(\d{1,3}):([0-5]\d):([0-5]\d)", re.ASCII)


def gtfs_time_to_secs(value: str | None) -> int | None:
    """Return seconds since the start of the service day for a GTFS time ("25:10:00" -> 90600).

    GTFS times can exceed 24:00, so they are never parsed as clock times. Empty gives None.
    """
    if value is None or not value.strip():
        return None
    match = _TIME.fullmatch(value.strip())
    if match is None:
        raise ValueError(f"not a GTFS time: {value!r}")
    hours, minutes, seconds = (int(part) for part in match.groups())
    return hours * 3600 + minutes * 60 + seconds


def is_berlin_stop(stop_id: str) -> bool:
    """Return True if the stop_id is in the state of Berlin."""
    return stop_id.startswith(BERLIN_STOP_PREFIX)


class GtfsSource:
    """A static GTFS feed, either a zip file or a folder of .txt files."""

    def __init__(self, path: Path) -> None:
        """Wrap a zip file or a folder."""
        self.path = path

    def names(self) -> set[str]:
        """Return the file names in the feed, e.g. {"stops.txt", ...}."""
        if self.path.is_dir():
            return {p.name for p in self.path.glob("*.txt")}
        with zipfile.ZipFile(self.path) as z:
            return set(z.namelist())

    def sha256(self) -> str:
        """Return the SHA-256 of the zip, or of the folder's file names and contents in order."""
        digest = hashlib.sha256()
        if self.path.is_dir():
            for name in sorted(self.names()):
                digest.update(name.encode() + b"\0")
                digest.update((self.path / name).read_bytes())
            return digest.hexdigest()
        with self.path.open("rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @contextmanager
    def _open(self, name: str) -> Iterator[TextIO]:
        """Open one file of the feed as text, without a byte order mark."""
        if self.path.is_dir():
            with (self.path / name).open(encoding="utf-8-sig", newline="") as f:
                yield f
        else:
            with zipfile.ZipFile(self.path) as z, z.open(name) as raw:
                yield io.TextIOWrapper(raw, encoding="utf-8-sig", newline="")

    def rows(self, name: str, columns: Sequence[str]) -> Iterator[list[str | None]]:
        """Stream one file's values for the given columns; missing columns and "" become None."""
        with self._open(name) as f:
            reader = csv.reader(f)
            header = [h.strip() for h in next(reader, [])]
            index = [header.index(c) if c in header else None for c in columns]
            for record in reader:
                if not record:
                    continue
                yield [
                    (record[i] or None) if i is not None and i < len(record) else None
                    for i in index
                ]


def plain_rows(source: GtfsSource, table: str, version_id: str) -> Iterator[Row]:
    """Rows for a table without derived columns."""
    for values in source.rows(f"{table}.txt", COLUMNS[table]):
        yield (version_id, *values)


def stop_time_rows(source: GtfsSource, version_id: str, berlin_trips: set[str]) -> Iterator[Row]:
    """Rows for stop_times with seconds; adds every trip with a Berlin stop to berlin_trips."""
    for values in source.rows("stop_times.txt", COLUMNS["stop_times"]):
        trip_id, stop_id, _, arrival, departure = values[:5]
        if stop_id and trip_id and is_berlin_stop(stop_id):
            berlin_trips.add(trip_id)
        yield (version_id, *values, gtfs_time_to_secs(arrival), gtfs_time_to_secs(departure))


def trip_rows(
    source: GtfsSource, version_id: str, berlin_trips: set[str], berlin_routes: set[str]
) -> Iterator[Row]:
    """Rows for trips with in_berlin; adds the route of every Berlin trip to berlin_routes."""
    for values in source.rows("trips.txt", COLUMNS["trips"]):
        route_id, trip_id = values[0], values[2]
        in_berlin = trip_id in berlin_trips
        if in_berlin and route_id:
            berlin_routes.add(route_id)
        yield (version_id, *values, in_berlin)


def route_rows(source: GtfsSource, version_id: str, berlin_routes: set[str]) -> Iterator[Row]:
    """Rows for routes with mode and in_berlin."""
    for values in source.rows("routes.txt", COLUMNS["routes"]):
        route_id, route_type = values[0], values[4]
        if route_type is None:
            raise ValueError(f"route {route_id!r} has no route_type")
        mode = mode_for_route_type(int(route_type))
        yield (version_id, *values, mode.value, route_id in berlin_routes)


def _gtfs_date(value: str | None) -> date | None:
    """Parse a GTFS date (YYYYMMDD); None if empty or malformed."""
    try:
        return datetime.strptime(value.strip(), "%Y%m%d").date() if value else None
    except ValueError:
        return None


def feed_start_date(source: GtfsSource, names: set[str]) -> date | None:
    """Return the feed's first date without loading it, by the same rule as the import.

    feed_info's feed_start_date if set, else the earliest date in calendar and calendar_dates.
    Reads only those small files, so a refresh can skip a feed that starts too far ahead.
    """
    if "feed_info.txt" in names:
        starts = [_gtfs_date(v[0]) for v in source.rows("feed_info.txt", ["feed_start_date"])]
        if found := [d for d in starts if d is not None]:
            return min(found)
    dates: list[date] = []
    if "calendar.txt" in names:
        for start, end in source.rows("calendar.txt", ["start_date", "end_date"]):
            dates += [d for d in (_gtfs_date(start), _gtfs_date(end)) if d is not None]
    if "calendar_dates.txt" in names:
        for (value,) in source.rows("calendar_dates.txt", ["date"]):
            if (d := _gtfs_date(value)) is not None:
                dates.append(d)
    return min(dates, default=None)
