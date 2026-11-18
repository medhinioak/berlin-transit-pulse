"""Record full VBB GTFS-RT snapshots into fixtures/local/ (never committed).

Polite to the feed: at most one request every 10 s (the default interval is 30 s), gzip,
If-None-Match and a User-Agent with contact details from BTP_FEED_CONTACT.

    BTP_FEED_CONTACT=<url or email> uv run python scripts/record_fixtures.py --label rush-hour
"""

import argparse
import gzip
import json
import os
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import httpx
from google.transit import gtfs_realtime_pb2

FEED_URL = "https://production.gtfsrt.vbb.de/data"
MIN_INTERVAL_S = 10.0
MAX_RETRIES = 3


class Pacer:
    """Blocks so that two requests are never closer together than the interval."""

    def __init__(
        self,
        interval_s: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """Refuse intervals below MIN_INTERVAL_S (VBB allows 7/min; we stay at 6/min or less)."""
        if interval_s < MIN_INTERVAL_S:
            raise ValueError(f"interval must be at least {MIN_INTERVAL_S} s, got {interval_s}")
        self.interval_s = interval_s
        self._clock = clock
        self._sleep = sleep
        self._last: float | None = None

    def wait(self, at_least_s: float = 0.0) -> None:
        """Sleep until the interval (or at_least_s, if longer) has passed since the last request."""
        if self._last is not None:
            due = self._last + max(self.interval_s, at_least_s)
            remaining = due - self._clock()
            if remaining > 0:
                self._sleep(remaining)
        self._last = self._clock()


def user_agent(contact: str) -> str:
    """Return the User-Agent VBB asks for: product, version and contact."""
    return f"berlin-transit-pulse/0.1 (+{contact})"


def retry_after_s(response: httpx.Response) -> float:
    """Return the Retry-After delay in seconds, or 0 if absent or not a number."""
    try:
        return max(0.0, float(response.headers.get("retry-after", "0")))
    except ValueError:
        return 0.0


def fetch_raw(
    client: httpx.Client, pacer: Pacer, etag: str | None
) -> tuple[int, bytes, str | None]:
    """GET the feed once (with retries on 429/5xx); return status, gzipped body and ETag."""
    headers = {"If-None-Match": etag} if etag else {}
    extra_wait = 0.0
    for attempt in range(MAX_RETRIES + 1):
        pacer.wait(extra_wait)
        with client.stream("GET", FEED_URL, headers=headers) as response:
            raw = b"".join(response.iter_raw())
            status = response.status_code
            if status == 429 or status >= 500:
                extra_wait = retry_after_s(response)
                print(f"  HTTP {status}, retry {attempt + 1}/{MAX_RETRIES}", file=sys.stderr)
                continue
            if status == 304:
                return status, b"", etag
            response.raise_for_status()
            if response.headers.get("content-encoding", "").lower() != "gzip":
                raw = gzip.compress(raw)
            return status, raw, response.headers.get("etag")
    raise RuntimeError(f"feed still failing after {MAX_RETRIES} retries")


def describe(raw_gz: bytes) -> str:
    """Summarise a gzipped snapshot: sizes, header timestamp and age, entity count."""
    data = gzip.decompress(raw_gz)
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(data)
    ts = feed.header.timestamp
    age = time.time() - ts
    when = datetime.fromtimestamp(ts, tz=UTC).isoformat()
    return (
        f"{len(raw_gz) / 1e6:.2f} MB gz, {len(data) / 1e6:.2f} MB raw, "
        f"feed ts {when} (age {age:.0f} s), {len(feed.entity)} entities"
    )


def main() -> int:
    """Record --count snapshots for one label."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--label", required=True, help="e.g. rush-hour or evening")
    parser.add_argument("--count", type=int, default=4)
    parser.add_argument("--interval", type=float, default=30.0, help="seconds, minimum 10")
    parser.add_argument("--out", type=Path, default=Path("fixtures/local/vbb-rt"))
    args = parser.parse_args()

    contact = os.environ.get("BTP_FEED_CONTACT", "").strip()
    if not contact:
        print("BTP_FEED_CONTACT is not set (a URL or email VBB can reach you at).", file=sys.stderr)
        return 2
    pacer = Pacer(args.interval)
    out = args.out / args.label
    out.mkdir(parents=True, exist_ok=True)
    # A set is one consecutive recording: drop snapshots from an earlier run of this label.
    for old in [*out.glob("*.pb.gz"), *out.glob("*.json")]:
        old.unlink()

    etag: str | None = None
    saved = 0
    headers = {"User-Agent": user_agent(contact), "Accept-Encoding": "gzip"}
    with httpx.Client(headers=headers, timeout=60.0) as client:
        while saved < args.count:
            status, raw, etag = fetch_raw(client, pacer, etag)
            if status == 304:
                print("  HTTP 304 not modified, skipped")
                continue
            now = datetime.now(UTC)
            stamp = now.strftime("%Y%m%dT%H%M%SZ")
            (out / f"{stamp}.pb.gz").write_bytes(raw)
            meta = {"fetched_at": now.isoformat(), "http_status": status, "etag": etag}
            (out / f"{stamp}.json").write_text(json.dumps(meta, indent=2) + "\n")
            saved += 1
            print(f"[{saved}/{args.count}] HTTP {status} {stamp}: {describe(raw)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
