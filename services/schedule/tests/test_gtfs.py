"""GTFS times, the Berlin rule and the row builders, on the mini fixture."""

import zipfile
from pathlib import Path

import pytest

from btp_common.fixtures import mini_gtfs_dir
from btp_schedule.gtfs import (
    COLUMNS,
    DERIVED,
    GtfsSource,
    gtfs_time_to_secs,
    is_berlin_stop,
    plain_rows,
    route_rows,
    stop_time_rows,
    trip_rows,
)

VERSION = "v1"


@pytest.mark.parametrize(
    ("text", "secs"),
    [
        ("00:00:00", 0),
        ("05:03:07", 18187),
        ("5:03:07", 18187),
        ("23:59:59", 86399),
        ("24:00:00", 86400),
        ("25:10:00", 90600),
        (" 25:10:00 ", 90600),
        ("", None),
        (None, None),
    ],
)
def test_gtfs_time_to_secs(text: str | None, secs: int | None) -> None:
    """Times past 24:00 count on from the start of the service day."""
    assert gtfs_time_to_secs(text) == secs


@pytest.mark.parametrize("text", ["25:10", "12:60:00", "12:00:61", "ab:cd:ef", "1:2:3"])
def test_gtfs_time_to_secs_rejects_malformed(text: str) -> None:
    """Anything that isn't H:MM:SS is an error, not a guess."""
    with pytest.raises(ValueError):
        gtfs_time_to_secs(text)


@pytest.mark.parametrize(
    ("stop_id", "berlin"),
    [
        ("de:11000:900007104::2", True),
        ("de:11000:900007104", True),
        ("de:12070:900215110:1:50", False),
        ("900007104", False),
        ("de:110001:x", False),
    ],
)
def test_is_berlin_stop(stop_id: str, berlin: bool) -> None:
    """Only stop_ids in Berlin's state code (de:11000:) count."""
    assert is_berlin_stop(stop_id) is berlin


@pytest.fixture(params=["folder", "zip"])
def mini(request: pytest.FixtureRequest, tmp_path: Path) -> GtfsSource:
    """The mini GTFS as a folder and as a zip."""
    if request.param == "folder":
        return GtfsSource(mini_gtfs_dir())
    path = tmp_path / "mini.zip"
    with zipfile.ZipFile(path, "w") as z:
        for file in mini_gtfs_dir().glob("*.txt"):
            z.write(file, file.name)
    return GtfsSource(path)


def test_berlin_flags_on_mini(mini: GtfsSource) -> None:
    """Trips with a Berlin stop and their routes are in Berlin; Brandenburg-only ones aren't."""
    berlin_trips: set[str] = set()
    berlin_routes: set[str] = set()
    stop_times = list(stop_time_rows(mini, VERSION, berlin_trips))
    trips = {r[3]: r for r in trip_rows(mini, VERSION, berlin_trips, berlin_routes)}
    routes = {r[1]: r for r in route_rows(mini, VERSION, berlin_routes)}

    assert len(stop_times) == 7497
    assert trips["311455976"][-1] is True  # M10, Berlin tram
    assert trips["309321194"][-1] is False  # RE1, a trip that stays in Brandenburg
    assert trips["309321104"][-1] is True  # RE1 through Berlin
    re1 = next(r for r in routes.values() if r[3] == "RE1")
    assert re1[-2:] == ("regional", True)
    assert {r[-2] for r in routes.values()} <= {"bus", "tram", "ubahn", "sbahn", "regional"}
    assert 0 < len(berlin_routes) < len(routes)


def test_rows_match_table_columns(mini: GtfsSource) -> None:
    """Every row has version_id, the source columns and the derived columns, in that order."""
    trips: set[str] = set()
    routes: set[str] = set()
    builders = {
        "stop_times": stop_time_rows(mini, VERSION, trips),
        "trips": trip_rows(mini, VERSION, trips, routes),
        "routes": route_rows(mini, VERSION, routes),
        "stops": plain_rows(mini, "stops", VERSION),
        "calendar": plain_rows(mini, "calendar", VERSION),
    }
    for table, rows in builders.items():
        row = next(iter(rows))
        assert row[0] == VERSION
        assert len(row) == 1 + len(COLUMNS[table]) + len(DERIVED.get(table, ()))


def test_missing_columns_and_empty_values_are_none(tmp_path: Path) -> None:
    """A column absent from the file and an empty value both become None; a BOM is stripped."""
    (tmp_path / "agency.txt").write_text("﻿agency_id,agency_name\n1,S-Bahn\n2,\n", encoding="utf-8")
    rows = list(plain_rows(GtfsSource(tmp_path), "agency", VERSION))
    assert rows == [
        (VERSION, "1", "S-Bahn", None, None, None, None),
        (VERSION, "2", None, None, None, None, None),
    ]


def test_route_without_type_is_an_error(tmp_path: Path) -> None:
    """A route without route_type can't get a mode, so the import fails."""
    (tmp_path / "routes.txt").write_text("route_id,route_type\nr1,\n", encoding="utf-8")
    with pytest.raises(ValueError, match="route_type"):
        list(route_rows(GtfsSource(tmp_path), VERSION, set()))


def test_sha256_changes_with_content(tmp_path: Path) -> None:
    """The version id is stable for the same files and changes when one row changes."""
    (tmp_path / "agency.txt").write_text("agency_id\n1\n", encoding="utf-8")
    first = GtfsSource(tmp_path).sha256()
    assert GtfsSource(tmp_path).sha256() == first
    (tmp_path / "agency.txt").write_text("agency_id\n2\n", encoding="utf-8")
    assert GtfsSource(tmp_path).sha256() != first
