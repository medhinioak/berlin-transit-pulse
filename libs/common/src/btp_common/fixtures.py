"""Load the committed feed fixtures (fixtures/ in the repo) for tests and fixtures mode."""

import gzip
import os
from pathlib import Path

from google.transit.gtfs_realtime_pb2 import FeedMessage


def fixtures_dir() -> Path:
    """Return the fixtures folder: BTP_FIXTURES_DIR, or fixtures/ at the repo root."""
    override = os.environ.get("BTP_FIXTURES_DIR")
    if override:
        return Path(override)
    # Works for the editable workspace install (tests, scripts): libs/common/src/btp_common/
    # fixtures.py -> repo root is four levels up. Installed in an image, the package sits in
    # site-packages, so containers must set BTP_FIXTURES_DIR (the compose service does, #7).
    return Path(__file__).resolve().parents[4] / "fixtures"


def rt_fixture_paths(label: str) -> list[Path]:
    """Return the gzipped snapshot files of one set (e.g. rush-hour), in recording order."""
    paths = sorted((fixtures_dir() / "vbb-rt" / label).glob("*.pb.gz"))
    if not paths:
        raise FileNotFoundError(
            f"no realtime fixtures for label {label!r} in {fixtures_dir()} "
            "(outside the repo checkout, set BTP_FIXTURES_DIR)"
        )
    return paths


def load_rt_fixture(label: str) -> list[FeedMessage]:
    """Return the trimmed realtime snapshots of one set (e.g. rush-hour), in recording order."""
    feeds = []
    for path in rt_fixture_paths(label):
        feed = FeedMessage()
        feed.ParseFromString(gzip.decompress(path.read_bytes()))
        feeds.append(feed)
    return feeds


def mini_gtfs_dir() -> Path:
    """Return the folder with the mini static GTFS that matches the realtime fixtures."""
    return fixtures_dir() / "gtfs-static" / "mini"
