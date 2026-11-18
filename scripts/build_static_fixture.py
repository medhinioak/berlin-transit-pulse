"""Build the mini static GTFS: exactly the rows the committed realtime fixtures reference.

Reads a cached VBB static GTFS zip (downloading it first if missing) and the trimmed realtime
fixtures, then writes plain CSV files to fixtures/gtfs-static/mini/.

    uv run python scripts/build_static_fixture.py
"""

import argparse
import csv
import gzip
import hashlib
import io
import json
import os
import sys
import zipfile
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path

import httpx
from google.transit import gtfs_realtime_pb2

STATIC_URL = "https://www.vbb.de/vbbgtfs"


def rt_references(rt_dir: Path) -> tuple[set[str], set[str]]:
    """Return the trip_ids and stop_ids used by every trimmed realtime fixture under rt_dir."""
    trip_ids: set[str] = set()
    stop_ids: set[str] = set()
    for path in sorted(rt_dir.glob("*/*.pb.gz")):
        feed = gtfs_realtime_pb2.FeedMessage()
        feed.ParseFromString(gzip.decompress(path.read_bytes()))
        for entity in feed.entity:
            trip_ids.add(entity.trip_update.trip.trip_id)
            stop_ids.update(s.stop_id for s in entity.trip_update.stop_time_update if s.stop_id)
    return trip_ids, stop_ids


def rows(z: zipfile.ZipFile, name: str) -> Iterator[dict[str, str]]:
    """Stream the rows of one GTFS file from the zip."""
    with z.open(name) as f:
        yield from csv.DictReader(io.TextIOWrapper(f, "utf-8-sig", newline=""))


def copy_filtered(
    z: zipfile.ZipFile, name: str, out_dir: Path, keep: Callable[[dict[str, str]], bool]
) -> list[dict[str, str]]:
    """Write the rows of a GTFS file that pass keep, with the original header; return them."""
    with z.open(name) as f:
        header = next(csv.reader(io.TextIOWrapper(f, "utf-8-sig", newline="")))
    kept = [r for r in rows(z, name) if keep(r)]
    with (out_dir / name).open("w", newline="", encoding="utf-8") as out:
        writer = csv.DictWriter(out, fieldnames=header, lineterminator="\n")
        writer.writeheader()
        writer.writerows(kept)
    return kept


def build(gtfs_zip: Path, rt_dir: Path, out_dir: Path) -> tuple[dict[str, int], list[str]]:
    """Write the mini GTFS for the realtime fixtures.

    Returns row counts per file and the realtime stop_ids missing from the static stops.txt
    (VBB sends some stops as bare numbers, e.g. 900162001, instead of de:11000:900162001::1).
    """
    trip_ids, rt_stop_ids = rt_references(rt_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.txt"):
        old.unlink()
    with zipfile.ZipFile(gtfs_zip) as z:
        names = set(z.namelist())
        trips = copy_filtered(z, "trips.txt", out_dir, lambda r: r["trip_id"] in trip_ids)
        missing = trip_ids - {t["trip_id"] for t in trips}
        if missing:
            raise SystemExit(f"{len(missing)} realtime trips are not in the static GTFS")
        route_ids = {t["route_id"] for t in trips}
        service_ids = {t["service_id"] for t in trips}
        stop_times = copy_filtered(z, "stop_times.txt", out_dir, lambda r: r["trip_id"] in trip_ids)
        stop_ids = {s["stop_id"] for s in stop_times} | rt_stop_ids
        parents = {
            r["parent_station"] for r in rows(z, "stops.txt") if r["stop_id"] in stop_ids
        } - {""}
        wanted_stops = stop_ids | parents
        stops = copy_filtered(z, "stops.txt", out_dir, lambda r: r["stop_id"] in wanted_stops)
        unknown_rt_stops = sorted(rt_stop_ids - {s["stop_id"] for s in stops})
        routes = copy_filtered(z, "routes.txt", out_dir, lambda r: r["route_id"] in route_ids)
        agency_ids = {r["agency_id"] for r in routes}
        copy_filtered(z, "agency.txt", out_dir, lambda r: r["agency_id"] in agency_ids)
        for name in ("calendar.txt", "calendar_dates.txt"):
            copy_filtered(z, name, out_dir, lambda r: r["service_id"] in service_ids)
        if "feed_info.txt" in names:
            copy_filtered(z, "feed_info.txt", out_dir, lambda r: True)
    counts = {
        p.name: sum(1 for _ in p.open(encoding="utf-8")) - 1 for p in sorted(out_dir.glob("*.txt"))
    }
    return counts, unknown_rt_stops


def download(path: Path) -> None:
    """Download the static GTFS zip once, politely, to the cache path."""
    contact = os.environ.get("BTP_FEED_CONTACT", "").strip()
    if not contact:
        raise SystemExit("BTP_FEED_CONTACT is not set (needed to download the static GTFS)")
    path.parent.mkdir(parents=True, exist_ok=True)
    headers = {"User-Agent": f"berlin-transit-pulse/0.1 (+{contact})"}
    with httpx.stream(
        "GET", STATIC_URL, headers=headers, follow_redirects=True, timeout=300.0
    ) as response:
        response.raise_for_status()
        with path.open("wb") as f:
            for chunk in response.iter_bytes():
                f.write(chunk)


def main() -> int:
    """Build fixtures/gtfs-static/mini and its manifest."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--gtfs-zip", type=Path, default=Path("fixtures/local/gtfs-static/vbb-gtfs.zip")
    )
    parser.add_argument("--rt-dir", type=Path, default=Path("fixtures/vbb-rt"))
    parser.add_argument("--out", type=Path, default=Path("fixtures/gtfs-static/mini"))
    args = parser.parse_args()

    if not args.gtfs_zip.exists():
        print(f"downloading {STATIC_URL} to {args.gtfs_zip}")
        download(args.gtfs_zip)
    counts, unknown_rt_stops = build(args.gtfs_zip, args.rt_dir, args.out)
    sha = hashlib.sha256(args.gtfs_zip.read_bytes()).hexdigest()
    zip_time = datetime.fromtimestamp(args.gtfs_zip.stat().st_mtime, tz=UTC)
    manifest = {
        "source": STATIC_URL,
        "licence": "Data: VBB Verkehrsverbund Berlin-Brandenburg GmbH, CC-BY 4.0",
        "zip_sha256": sha,
        "zip_downloaded_at": zip_time.isoformat(timespec="seconds"),
        "rows": counts,
        "rt_stop_ids_not_in_static": unknown_rt_stops,
    }
    (args.out.parent / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
