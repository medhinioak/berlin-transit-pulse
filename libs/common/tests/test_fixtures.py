"""Tests for the fixture loaders and the consistency of the committed fixtures."""

import csv
import json
from pathlib import Path

import pytest
from google.transit.gtfs_realtime_pb2 import FeedHeader

from btp_common.fixtures import fixtures_dir, load_rt_fixture, mini_gtfs_dir

SETS = ("rush-hour", "evening")
MODES = {"bus", "tram", "ubahn", "sbahn", "regional"}
BUDGET_BYTES = 2_000_000


def _column(name: str, column: str) -> set[str]:
    """Return all values of one column of a mini GTFS file."""
    with (mini_gtfs_dir() / name).open(encoding="utf-8", newline="") as f:
        return {row[column] for row in csv.DictReader(f)}


def _manifest(label: str) -> dict[str, object]:
    """Return the manifest of one realtime fixture set."""
    data: dict[str, object] = json.loads(
        (fixtures_dir() / "vbb-rt" / label / "manifest.json").read_text()
    )
    return data


def _static_manifest() -> dict[str, object]:
    """Return the manifest of the mini static GTFS."""
    data: dict[str, object] = json.loads(
        (fixtures_dir() / "gtfs-static" / "manifest.json").read_text()
    )
    return data


@pytest.mark.parametrize("label", SETS)
def test_set_has_four_consecutive_full_snapshots(label: str) -> None:
    """Each set is 4 FULL_DATASET snapshots with increasing feed timestamps."""
    feeds = load_rt_fixture(label)
    assert len(feeds) == 4
    assert all(f.header.incrementality == FeedHeader.FULL_DATASET for f in feeds)
    stamps = [f.header.timestamp for f in feeds]
    assert stamps == sorted(stamps) and len(set(stamps)) == 4


@pytest.mark.parametrize("label", SETS)
def test_realtime_matches_mini_static_gtfs(label: str) -> None:
    """Every realtime trip and route exists in the mini static GTFS; stops do, with one quirk.

    VBB sends some stops as bare numbers (e.g. 900162001) that aren't in stops.txt. The fixtures
    keep them on purpose; the static manifest lists them.
    """
    unknown = _static_manifest()["rt_stop_ids_not_in_static"]
    assert isinstance(unknown, list)
    trip_ids, route_ids, stop_ids = (
        _column("trips.txt", "trip_id"),
        _column("routes.txt", "route_id"),
        _column("stops.txt", "stop_id"),
    )
    for feed in load_rt_fixture(label):
        for entity in feed.entity:
            trip = entity.trip_update.trip
            assert trip.trip_id in trip_ids
            assert trip.route_id in route_ids
            for stu in entity.trip_update.stop_time_update:
                assert stu.stop_id in stop_ids or (stu.stop_id in unknown and stu.stop_id.isdigit())


def test_mini_static_gtfs_is_self_consistent() -> None:
    """stop_times, trips and calendars reference only rows that exist."""
    trip_ids = _column("trips.txt", "trip_id")
    assert _column("stop_times.txt", "trip_id") == trip_ids
    assert _column("stop_times.txt", "stop_id") <= _column("stops.txt", "stop_id")
    assert _column("trips.txt", "route_id") <= _column("routes.txt", "route_id")
    assert _column("routes.txt", "agency_id") <= _column("agency.txt", "agency_id")
    services = _column("calendar.txt", "service_id") | _column("calendar_dates.txt", "service_id")
    assert _column("trips.txt", "service_id") <= services


@pytest.mark.parametrize("label", SETS)
def test_set_covers_modes_and_edge_cases(label: str) -> None:
    """Running trips of every mode, a cancelled trip and an appearing or vanishing trip."""
    counts = _manifest(label)["counts"]
    assert isinstance(counts, dict)
    assert set(counts["running"]) == MODES
    assert counts.get("cancelled")
    assert counts.get("appears") or counts.get("disappears")


def test_committed_fixtures_fit_the_budget() -> None:
    """Everything committed under fixtures/ (not fixtures/local/) stays under 2 MB."""
    root = fixtures_dir()
    local = root / "local"
    size = sum(p.stat().st_size for p in root.rglob("*") if p.is_file() and local not in p.parents)
    assert size < BUDGET_BYTES


def test_unknown_label_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing set is an error, not an empty list."""
    monkeypatch.setenv("BTP_FIXTURES_DIR", str(tmp_path))
    with pytest.raises(FileNotFoundError):
        load_rt_fixture("rush-hour")
