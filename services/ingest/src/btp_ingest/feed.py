"""Fetch, parse and validate GTFS-RT snapshots: politely from VBB, or replayed from fixtures."""

import gzip
import json
import logging
import math
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from importlib.metadata import version
from pathlib import Path

import httpx
from google.protobuf.message import DecodeError
from google.transit.gtfs_realtime_pb2 import FeedHeader, FeedMessage

from btp_common.fixtures import rt_fixture_paths

log = logging.getLogger(__name__)

REQUEST_TIMEOUT_S = 20.0
SUPPORTED_VERSIONS = ("1.0", "2.0")
# VBB allows 7 requests per minute; we never send more than 6 in any 60 s, retries included.
MAX_REQUESTS_PER_WINDOW = 6
RATE_WINDOW_S = 60.0
BACKOFF_INITIAL_S = 30.0
BACKOFF_CAP_S = 300.0
# Retry-After is honoured above the backoff cap, but not beyond this (an `inf` or a bogus
# date would otherwise overflow the wait and crash the loop).
RETRY_AFTER_MAX_S = 3600.0

Clock = Callable[[], float]
Sleep = Callable[[float], None]
WallClock = Callable[[], datetime]


def now_utc() -> datetime:
    """Return the current time in UTC."""
    return datetime.now(UTC)


@dataclass(frozen=True)
class Snapshot:
    """One validated feed snapshot and how it was fetched."""

    feed_timestamp: datetime
    fetched_at: datetime
    etag: str | None
    raw_bytes_len: int  # decompressed protobuf size
    wire_bytes: int  # bytes received, as sent (gzipped)
    message: FeedMessage


class FeedUnavailable(Exception):
    """The feed answered 429, 5xx or another error status, or couldn't be reached."""

    def __init__(self, status: str, retry_after_s: float = 0.0, detail: str = "") -> None:
        """Keep the status ('429', '503', 'network', …), Retry-After and any error detail."""
        super().__init__(f"feed unavailable: {status}")
        self.status = status
        self.retry_after_s = retry_after_s
        self.detail = detail


class InvalidSnapshot(Exception):
    """The body isn't a valid GTFS-RT FeedMessage, or its header fails validation."""


class RateGuard:
    """Sliding-window limit: at most max_requests in any window_s, whatever the caller does."""

    def __init__(
        self,
        max_requests: int = MAX_REQUESTS_PER_WINDOW,
        window_s: float = RATE_WINDOW_S,
        clock: Clock = time.monotonic,
        sleep: Sleep = time.sleep,
    ) -> None:
        """Start with an empty window."""
        self.max_requests = max_requests
        self.window_s = window_s
        self._clock = clock
        self._sleep = sleep
        self._sent: deque[float] = deque()

    def acquire(self) -> None:
        """Block until a request is allowed, then record it."""
        while True:
            now = self._clock()
            while self._sent and self._sent[0] <= now - self.window_s:
                self._sent.popleft()
            if len(self._sent) < self.max_requests:
                self._sent.append(now)
                return
            wait = self._sent[0] + self.window_s - now
            log.warning("rate guard: waiting %.1f s before the next request", wait)
            self._sleep(wait)


class Backoff:
    """Exponential backoff after failures, honouring the server's Retry-After."""

    def __init__(self, initial_s: float = BACKOFF_INITIAL_S, cap_s: float = BACKOFF_CAP_S) -> None:
        """Start with no failures."""
        self.initial_s = initial_s
        self.cap_s = cap_s
        self.failures = 0

    def next(self, retry_after_s: float = 0.0) -> float:
        """Record a failure and return the delay before the next attempt.

        Retry-After wins when it's longer, even above the cap: it's VBB's explicit request.
        """
        delay = min(self.initial_s * 2.0**self.failures, self.cap_s)
        self.failures += 1
        return max(delay, retry_after_s)

    def reset(self) -> None:
        """Forget earlier failures after a successful poll."""
        self.failures = 0


def retry_after_s(value: str | None, now: datetime) -> float:
    """Parse a Retry-After header (seconds or HTTP date), clamped to 0..RETRY_AFTER_MAX_S.

    Absent, unparseable or non-finite values give 0.
    """
    if not value:
        return 0.0
    try:
        seconds = float(value)
    except ValueError:
        try:
            seconds = (parsedate_to_datetime(value) - now).total_seconds()
        except (TypeError, ValueError):
            return 0.0
    if not math.isfinite(seconds):
        return 0.0
    return min(max(0.0, seconds), RETRY_AFTER_MAX_S)


def user_agent(contact: str) -> str:
    """Return the User-Agent VBB asks for: product, version and contact."""
    return f"berlin-transit-pulse/{version('btp-ingest')} (+{contact})"


def parse(data: bytes) -> FeedMessage:
    """Decode a protobuf FeedMessage, raising InvalidSnapshot on garbage."""
    message = FeedMessage()
    try:
        message.ParseFromString(data)
    except DecodeError as exc:
        raise InvalidSnapshot(f"not a FeedMessage: {exc}") from exc
    return message


def validate(message: FeedMessage, now: datetime, max_age_s: float) -> datetime:
    """Check the header and return its timestamp; a stale header only logs a warning."""
    header = message.header
    if header.gtfs_realtime_version not in SUPPORTED_VERSIONS:
        raise InvalidSnapshot(f"unsupported gtfs_realtime_version {header.gtfs_realtime_version!r}")
    # proto2 default for incrementality is FULL_DATASET, which VBB sends.
    if header.incrementality != FeedHeader.FULL_DATASET:
        raise InvalidSnapshot("incrementality is not FULL_DATASET")
    if not header.timestamp:
        raise InvalidSnapshot("header timestamp missing")
    try:
        feed_timestamp = datetime.fromtimestamp(header.timestamp, tz=UTC)
    except (OverflowError, OSError, ValueError) as exc:
        raise InvalidSnapshot(f"header timestamp {header.timestamp} out of range") from exc
    age_s = (now - feed_timestamp).total_seconds()
    if age_s > max_age_s:
        log.warning("stale feed: header is %.0f s old (limit %.0f s)", age_s, max_age_s)
    return feed_timestamp


class FeedClient:
    """Polls the live feed with gzip, ETag, a contact User-Agent and the rate guard."""

    def __init__(
        self,
        url: str,
        contact: str | None,
        max_age_s: float,
        *,
        transport: httpx.BaseTransport | None = None,
        clock: Clock = time.monotonic,
        sleep: Sleep = time.sleep,
        wall: WallClock = now_utc,
    ) -> None:
        """Refuse to start without a contact: VBB blocks anonymous clients."""
        if not contact or not contact.strip():
            raise ValueError("BTP_FEED_CONTACT must be set in live mode (an email or URL)")
        self.url = url
        self.max_age_s = max_age_s
        self.guard = RateGuard(clock=clock, sleep=sleep)
        self._wall = wall
        self._etag: str | None = None
        self._client = httpx.Client(
            headers={"User-Agent": user_agent(contact.strip()), "Accept-Encoding": "gzip"},
            timeout=REQUEST_TIMEOUT_S,
            transport=transport,
        )

    def fetch(self) -> Snapshot | None:
        """Request the feed once; None means 304 Not Modified."""
        self.guard.acquire()
        headers = {"If-None-Match": self._etag} if self._etag else {}
        try:
            with self._client.stream("GET", self.url, headers=headers) as response:
                status = response.status_code
                response_headers = response.headers
                # The body as sent, so the log shows bytes on the wire; decoded below.
                wire = b"".join(response.iter_raw()) if status == 200 else b""
        except httpx.TransportError as exc:
            raise FeedUnavailable("network", detail=f"{type(exc).__name__}: {exc}") from exc
        fetched_at = self._wall()
        if status == 304:
            return None
        if status != 200:
            retry = retry_after_s(response_headers.get("retry-after"), fetched_at)
            raise FeedUnavailable(str(status), retry)
        data = _decode(wire, response_headers.get("content-encoding", ""))
        message = parse(data)
        feed_timestamp = validate(message, fetched_at, self.max_age_s)
        # Only a valid snapshot moves the ETag, so a bad body is fetched again next time.
        self._etag = response_headers.get("etag")
        return Snapshot(
            feed_timestamp=feed_timestamp,
            fetched_at=fetched_at,
            etag=self._etag,
            raw_bytes_len=len(data),
            wire_bytes=len(wire),
            message=message,
        )

    def close(self) -> None:
        """Close the HTTP connection pool."""
        self._client.close()


class FixtureSource:
    """Replays a recorded fixture set in order, looping, with the header time set to now."""

    def __init__(self, fixture_set: str, max_age_s: float, *, wall: WallClock = now_utc) -> None:
        """Load the set's snapshots and their ETags; fail if the set doesn't exist."""
        paths = rt_fixture_paths(fixture_set)
        etags = _manifest_etags(paths[0].parent / "manifest.json")
        self._items = [(p.read_bytes(), etags.get(p.name)) for p in paths]
        self.max_age_s = max_age_s
        self._wall = wall
        self._next = 0

    def fetch(self) -> Snapshot:
        """Return the next snapshot of the set, restarting after the last one."""
        compressed, etag = self._items[self._next]
        self._next = (self._next + 1) % len(self._items)
        data = _decode(compressed, "gzip")
        message = parse(data)
        fetched_at = self._wall()
        # Downstream logic treats the snapshot as live; trip-level times stay as recorded.
        message.header.timestamp = int(fetched_at.timestamp())
        feed_timestamp = validate(message, fetched_at, self.max_age_s)
        return Snapshot(
            feed_timestamp=feed_timestamp,
            fetched_at=fetched_at,
            etag=etag,
            raw_bytes_len=len(data),
            wire_bytes=len(compressed),
            message=message,
        )

    def close(self) -> None:
        """Nothing to release; here so both sources look alike."""


def _decode(wire: bytes, content_encoding: str) -> bytes:
    """Undo gzip content encoding; a corrupt gzip body is an invalid snapshot."""
    if content_encoding.strip().lower() != "gzip":
        return wire
    try:
        return gzip.decompress(wire)
    except (OSError, EOFError) as exc:
        raise InvalidSnapshot(f"corrupt gzip body: {exc}") from exc


def _manifest_etags(path: Path) -> dict[str, str]:
    """Map snapshot file names to their recorded ETag; empty if there's no manifest."""
    if not path.exists():
        return {}
    manifest = json.loads(path.read_text())
    return {s["file"]: s["etag"] for s in manifest.get("snapshots", []) if s.get("etag")}
