"""Trim a folder of full VBB snapshots into small, consistent test fixtures plus a manifest.

Trips are keyed by (trip_id, start_date). Most picks are trips running at snapshot time, per
mode, because trips that haven't started carry no real delay. A few future, cancelled,
starting, ending, appearing and disappearing trips are added. Selection is deterministic:
candidates are ordered by a seeded hash of the key.

    make fixtures LABEL=rush-hour   # records, then runs this script and the static build
"""

import argparse
import csv
import gzip
import hashlib
import io
import json
import sys
import zipfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from build_static_fixture import ensure_zip
from google.transit import gtfs_realtime_pb2

CANCELED = gtfs_realtime_pb2.TripDescriptor.CANCELED

# Per-mode picks for trips in every snapshot. Sized so both sets plus the mini static GTFS
# (stops and stop_times dominate) stay under the 2 MB fixture budget.
PER_MODE = {"running": 25, "future": 3}
# Picks across all modes for the edge cases.
EDGE_CASES = {"cancelled": 8, "starts": 6, "ends": 6, "appears": 6, "disappears": 6}

TripKey = tuple[str, str]


def mode_for_route_type(route_type: int) -> str:
    """Map VBB's extended GTFS route types to a mode (same rules as btp_schedule, #5)."""
    if route_type == 109:
        return "sbahn"
    if 400 <= route_type <= 499 or route_type == 1:
        return "ubahn"
    if 700 <= route_type <= 799 or 200 <= route_type <= 299 or route_type == 3:
        return "bus"
    if 900 <= route_type <= 999 or route_type == 0:
        return "tram"
    if 100 <= route_type <= 199 or route_type == 2:
        return "regional"
    if 1000 <= route_type <= 1099 or 1200 <= route_type <= 1299 or route_type == 4:
        return "ferry"
    return "other"


def trip_key(entity: gtfs_realtime_pb2.FeedEntity) -> TripKey:
    """Return the trip instance key: trip_id and service start date."""
    trip = entity.trip_update.trip
    return trip.trip_id, trip.start_date


def phase(entity: gtfs_realtime_pb2.FeedEntity, at: int) -> str:
    """Classify a trip at a feed timestamp: cancelled, future, running, finished or unknown."""
    tu = entity.trip_update
    if tu.trip.schedule_relationship == CANCELED:
        return "cancelled"
    times = [
        ev.time
        for stu in tu.stop_time_update
        for ev in (stu.arrival, stu.departure)
        if ev.HasField("time")
    ]
    if not times:
        return "unknown"
    if min(times) > at:
        return "future"
    if max(times) < at:
        return "finished"
    return "running"


def category(phases: list[str | None]) -> str | None:
    """Pick one category for a trip from its phase in each snapshot (None means absent)."""
    first, last = phases[0], phases[-1]
    if first is None and last is not None:
        return "appears"
    if first is not None and last is None:
        return "disappears"
    if None in phases:
        return None
    if "cancelled" in phases:
        return "cancelled"
    if first == "future" and last == "running":
        return "starts"
    if first == "running" and last == "finished":
        return "ends"
    if all(p == "running" for p in phases):
        return "running"
    if all(p == "future" for p in phases):
        return "future"
    return None


def seeded_order(key: TripKey, seed: str) -> str:
    """Return a stable sort key that spreads picks across trip ids."""
    return hashlib.sha256(f"{seed}:{key[0]}:{key[1]}".encode()).hexdigest()


@dataclass
class Selection:
    """The chosen trips with their category and mode."""

    picks: dict[TripKey, tuple[str, str]]

    def counts(self) -> dict[str, dict[str, int]]:
        """Return pick counts per category and mode."""
        out: dict[str, Counter[str]] = {}
        for cat, mode in self.picks.values():
            out.setdefault(cat, Counter())[mode] += 1
        return {cat: dict(sorted(c.items())) for cat, c in sorted(out.items())}


def select(
    feeds: list[gtfs_realtime_pb2.FeedMessage],
    route_modes: dict[str, str],
    seed: str = "btp",
    per_mode: dict[str, int] = PER_MODE,
    edge_cases: dict[str, int] = EDGE_CASES,
) -> Selection:
    """Choose trips deterministically from consecutive snapshots."""
    phases: dict[TripKey, list[str | None]] = {}
    modes: dict[TripKey, str] = {}
    for i, feed in enumerate(feeds):
        for entity in feed.entity:
            if not entity.HasField("trip_update"):
                continue
            key = trip_key(entity)
            phases.setdefault(key, [None] * len(feeds))[i] = phase(entity, feed.header.timestamp)
            modes[key] = route_modes.get(entity.trip_update.trip.route_id, "other")

    by_cat: dict[str, list[TripKey]] = {}
    for key, ps in phases.items():
        cat = category(ps)
        if cat is not None:
            by_cat.setdefault(cat, []).append(key)

    picks: dict[TripKey, tuple[str, str]] = {}
    for cat, quota in per_mode.items():
        for mode in sorted(set(modes.values())):
            pool = sorted(
                (k for k in by_cat.get(cat, []) if modes[k] == mode),
                key=lambda k: seeded_order(k, seed),
            )
            for key in pool[:quota]:
                picks[key] = (cat, mode)
    for cat, quota in edge_cases.items():
        pool = sorted(by_cat.get(cat, []), key=lambda k: seeded_order(k, seed))
        for key in pool[:quota]:
            picks[key] = (cat, modes[key])
    return Selection(picks)


def trimmed(
    feed: gtfs_realtime_pb2.FeedMessage, keys: set[TripKey]
) -> gtfs_realtime_pb2.FeedMessage:
    """Return a copy of the feed with the original header and only the chosen trips."""
    out = gtfs_realtime_pb2.FeedMessage()
    out.header.CopyFrom(feed.header)
    for entity in feed.entity:
        if entity.HasField("trip_update") and trip_key(entity) in keys:
            out.entity.add().CopyFrom(entity)
    return out


def read_route_modes(gtfs_zip: Path) -> dict[str, str]:
    """Read routes.txt from the static GTFS zip and map route_id to mode."""
    with zipfile.ZipFile(ensure_zip(gtfs_zip)) as z, z.open("routes.txt") as f:
        rows = csv.DictReader(io.TextIOWrapper(f, "utf-8-sig"))
        return {r["route_id"]: mode_for_route_type(int(r["route_type"])) for r in rows}


def main() -> int:
    """Trim one recorded set and write NN.pb.gz files plus manifest.json."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source", type=Path, help="folder with full <timestamp>.pb.gz snapshots")
    parser.add_argument(
        "--gtfs-zip", type=Path, default=Path("fixtures/local/gtfs-static/vbb-gtfs.zip")
    )
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--seed", default="btp")
    args = parser.parse_args()

    sources = sorted(args.source.glob("*.pb.gz"))
    if len(sources) < 2:
        print(f"need at least 2 snapshots in {args.source}", file=sys.stderr)
        return 2
    feeds = []
    for path in sources:
        feed = gtfs_realtime_pb2.FeedMessage()
        feed.ParseFromString(gzip.decompress(path.read_bytes()))
        feeds.append(feed)

    selection = select(feeds, read_route_modes(args.gtfs_zip), seed=args.seed)
    keys = set(selection.picks)
    args.out.mkdir(parents=True, exist_ok=True)
    for old in args.out.glob("*.pb.gz"):
        old.unlink()

    snapshots = []
    for i, (path, feed) in enumerate(zip(sources, feeds, strict=True), start=1):
        small = trimmed(feed, keys)
        raw = small.SerializeToString()
        gz = gzip.compress(raw, mtime=0)
        name = f"{i:02d}.pb.gz"
        (args.out / name).write_bytes(gz)
        meta_path = path.with_name(path.name.removesuffix(".pb.gz") + ".json")
        meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
        snapshots.append(
            {
                "file": name,
                "source": path.name,
                "feed_timestamp": feed.header.timestamp,
                "fetched_at": meta.get("fetched_at"),
                "etag": meta.get("etag"),
                "entities_full": len(feed.entity),
                "entities": len(small.entity),
                "bytes_full_gz": path.stat().st_size,
                "bytes_gz": len(gz),
                "bytes_raw": len(raw),
            }
        )

    edge = {
        cat: sorted(f"{k[0]}@{k[1]}" for k, (c, _) in selection.picks.items() if c == cat)
        for cat in EDGE_CASES
    }
    manifest = {
        "label": args.out.name,
        "source": "https://production.gtfsrt.vbb.de/data",
        "licence": "Data: VBB Verkehrsverbund Berlin-Brandenburg GmbH, CC-BY 4.0",
        "trim": {"seed": args.seed, "per_mode": PER_MODE, "edge_cases": EDGE_CASES},
        "trips": len(keys),
        "counts": selection.counts(),
        "edge_case_trips": edge,
        "snapshots": snapshots,
    }
    (args.out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"{args.out}: {len(keys)} trips, {sum(s['bytes_gz'] for s in snapshots) / 1e3:.0f} kB")
    print(json.dumps(selection.counts(), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
