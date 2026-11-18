"""Tests for the feed client, rate guard, backoff, validation and fixture replay."""

import gzip
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from google.transit.gtfs_realtime_pb2 import FeedHeader, FeedMessage

from btp_common.fixtures import load_rt_fixture
from btp_ingest.feed import (
    Backoff,
    FeedClient,
    FeedUnavailable,
    FixtureSource,
    InvalidSnapshot,
    RateGuard,
    retry_after_s,
    user_agent,
)

URL = "https://feed.test/data"
CONTACT = "ops@example.org"
# Header timestamp of the first rush-hour snapshot (fixtures/vbb-rt/rush-hour/manifest.json).
FEED_TS = 1795021401


class FakeClock:
    """Monotonic and wall clock in one; sleep() advances time instead of blocking."""

    def __init__(self) -> None:
        """Start 20 s after the first fixture's header, monotonic 0."""
        self.t = 0.0
        self.start = datetime.fromtimestamp(FEED_TS + 20, tz=UTC)
        self.sleeps: list[float] = []

    def now(self) -> float:
        """Return monotonic seconds."""
        return self.t

    def wall(self) -> datetime:
        """Return wall time in UTC."""
        return self.start + timedelta(seconds=self.t)

    def sleep(self, seconds: float) -> None:
        """Record the sleep and skip ahead."""
        self.sleeps.append(seconds)
        self.t += seconds


@pytest.fixture
def clock() -> FakeClock:
    """A fresh fake clock."""
    return FakeClock()


@pytest.fixture(scope="module")
def feed_bytes() -> bytes:
    """A real (trimmed) VBB snapshot, serialised."""
    return load_rt_fixture("rush-hour")[0].SerializeToString()


def feed_with(**header: object) -> bytes:
    """Serialise a minimal feed with a valid header, then the given header fields."""
    message = FeedMessage()
    message.header.gtfs_realtime_version = "2.0"
    message.header.timestamp = FEED_TS
    for key, value in header.items():
        setattr(message.header, key, value)
    return message.SerializeToString()


Answer = tuple[int, bytes, dict[str, str]]


def ok(body: bytes, etag: str | None = '"v1"') -> Answer:
    """A gzipped 200 answer, as VBB sends it."""
    headers = {"content-encoding": "gzip"}
    if etag:
        headers["etag"] = etag
    return 200, gzip.compress(body), headers


def status(code: int, **headers: str) -> Answer:
    """An answer with no body, e.g. 304 or 503."""
    return code, b"", {k.replace("_", "-"): v for k, v in headers.items()}


class Server:
    """A scripted MockTransport that records each request and the fake time it arrived."""

    def __init__(self, clock: FakeClock, *responses: Answer | Exception) -> None:
        """Answer with responses in order, repeating the last one."""
        self.clock = clock
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []
        self.times: list[float] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        """Record the request and return (or raise) the next scripted answer."""
        self.requests.append(request)
        self.times.append(self.clock.now())
        answer = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(answer, Exception):
            raise answer
        code, body, headers = answer
        # A streamed body, as from a real server (Response(content=...) arrives pre-read).
        return httpx.Response(code, headers=headers, stream=httpx.ByteStream(body))

    def client(self) -> FeedClient:
        """A FeedClient wired to this server and the fake clock."""
        return FeedClient(
            URL,
            CONTACT,
            max_age_s=180,
            transport=httpx.MockTransport(self),
            clock=self.clock.now,
            sleep=self.clock.sleep,
            wall=self.clock.wall,
        )


def test_sends_gzip_contact_and_etag_after_first_response(
    clock: FakeClock, feed_bytes: bytes
) -> None:
    """User-Agent carries the contact, gzip is requested, If-None-Match follows the ETag."""
    server = Server(clock, ok(feed_bytes, '"v1"'), status(304))
    client = server.client()
    snapshot = client.fetch()
    assert client.fetch() is None
    first, second = server.requests
    assert first.headers["user-agent"] == user_agent(CONTACT)
    assert CONTACT in first.headers["user-agent"]
    assert first.headers["user-agent"].startswith("berlin-transit-pulse/")
    assert "gzip" in first.headers["accept-encoding"]
    assert "if-none-match" not in first.headers
    assert second.headers["if-none-match"] == '"v1"'
    assert snapshot is not None
    assert snapshot.etag == '"v1"'
    assert snapshot.raw_bytes_len == len(feed_bytes)
    assert 0 < snapshot.wire_bytes < len(feed_bytes)
    assert snapshot.feed_timestamp == datetime.fromtimestamp(FEED_TS, tz=UTC)
    assert snapshot.fetched_at == clock.wall()
    assert len(snapshot.message.entity) > 100


def test_304_returns_none(clock: FakeClock) -> None:
    """Not Modified means no new data."""
    assert Server(clock, status(304)).client().fetch() is None


@pytest.mark.parametrize(
    ("response", "code", "retry_after"),
    [
        (status(429, retry_after="60"), "429", 60.0),
        (status(503), "503", 0.0),
        (status(500, retry_after="soon"), "500", 0.0),
        (status(403), "403", 0.0),
        (httpx.ConnectTimeout("timed out"), "network", 0.0),
    ],
)
def test_errors_raise_feed_unavailable(
    clock: FakeClock, response: Answer | Exception, code: str, retry_after: float
) -> None:
    """429, 5xx, other errors and network failures all raise FeedUnavailable."""
    with pytest.raises(FeedUnavailable) as info:
        Server(clock, response).client().fetch()
    assert info.value.status == code
    assert info.value.retry_after_s == retry_after


def test_retry_after_as_http_date() -> None:
    """Retry-After may also be an HTTP date."""
    now = datetime(2026, 11, 19, 12, 0, tzinfo=UTC)
    assert retry_after_s("Thu, 19 Nov 2026 12:01:30 GMT", now) == 90.0
    assert retry_after_s(None, now) == 0.0


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("inf", 0.0),
        ("nan", 0.0),
        ("-5", 0.0),
        ("1e12", 3600.0),
        ("Fri, 31 Dec 9999 23:59:59 GMT", 3600.0),
    ],
)
def test_retry_after_is_clamped(value: str, expected: float) -> None:
    """Non-finite or absurd Retry-After values can't overflow the wait."""
    assert retry_after_s(value, datetime(2026, 11, 19, tzinfo=UTC)) == expected


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        (b"<html>maintenance</html>", "not a FeedMessage"),
        (feed_with(gtfs_realtime_version="3.0"), "unsupported gtfs_realtime_version"),
        (feed_with(incrementality=FeedHeader.DIFFERENTIAL), "FULL_DATASET"),
        (feed_with(timestamp=0), "timestamp missing"),
        (feed_with(timestamp=2**63), "out of range"),
    ],
)
def test_invalid_snapshots_raise_and_keep_the_etag(
    clock: FakeClock, feed_bytes: bytes, body: bytes, reason: str
) -> None:
    """Garbage or a bad header raises InvalidSnapshot and doesn't advance the ETag."""
    server = Server(clock, ok(feed_bytes, '"good"'), ok(body, '"bad"'), status(304))
    client = server.client()
    client.fetch()
    with pytest.raises(InvalidSnapshot, match=reason):
        client.fetch()
    client.fetch()
    assert server.requests[2].headers["if-none-match"] == '"good"'


def test_corrupt_gzip_is_invalid(clock: FakeClock) -> None:
    """A truncated gzip body is an invalid snapshot, not a crash."""
    truncated = (200, b"\x1f\x8b\x08\x00", {"content-encoding": "gzip"})
    with pytest.raises(InvalidSnapshot, match="corrupt gzip"):
        Server(clock, truncated).client().fetch()


def test_uncompressed_body_is_accepted(clock: FakeClock, feed_bytes: bytes) -> None:
    """If the server ignores Accept-Encoding, the plain body still parses."""
    snapshot = Server(clock, (200, feed_bytes, {})).client().fetch()
    assert snapshot is not None
    assert snapshot.wire_bytes == snapshot.raw_bytes_len == len(feed_bytes)


def test_version_1_0_is_accepted(clock: FakeClock) -> None:
    """GTFS-RT 1.0 feeds are valid too."""
    assert Server(clock, ok(feed_with(gtfs_realtime_version="1.0"))).client().fetch()


def test_stale_feed_warns_but_passes(clock: FakeClock, caplog: pytest.LogCaptureFixture) -> None:
    """A header older than the max age is logged and still returned."""
    clock.t = 600
    with caplog.at_level(logging.WARNING):
        snapshot = Server(clock, ok(feed_with())).client().fetch()
    assert snapshot is not None
    assert "stale feed" in caplog.text


@pytest.mark.parametrize("contact", [None, "", "  "])
def test_missing_contact_is_refused(contact: str | None) -> None:
    """Live mode can't start without contact details."""
    with pytest.raises(ValueError, match="BTP_FEED_CONTACT"):
        FeedClient(URL, contact, max_age_s=180)


def assert_at_most_6_per_minute(times: list[float]) -> None:
    """No 60 s window holds more than 6 of the request times."""
    for i, start in enumerate(times):
        assert len([t for t in times[i:] if t < start + 60]) <= 6, times


def test_rate_guard_never_allows_a_7th_request_within_60_s(clock: FakeClock) -> None:
    """A burst of 20 acquires is spread to 6 per 60 s."""
    guard = RateGuard(clock=clock.now, sleep=clock.sleep)
    times = []
    for _ in range(20):
        guard.acquire()
        times.append(clock.now())
    assert times[:6] == [0.0] * 6
    assert times[6] == 60.0
    assert_at_most_6_per_minute(times)


def test_rate_guard_holds_under_immediate_retries(clock: FakeClock) -> None:
    """Even a caller retrying 503s with no backoff at all sends at most 6 per minute."""
    server = Server(clock, status(503))
    client = server.client()
    for _ in range(25):
        with pytest.raises(FeedUnavailable):
            client.fetch()
        clock.t += 1  # the caller retries one second later
    assert len(server.times) == 25
    assert_at_most_6_per_minute(server.times)


def test_backoff_doubles_to_the_cap_and_resets() -> None:
    """30, 60, 120, 240, then capped at 300 s; reset starts again at 30."""
    backoff = Backoff()
    assert [backoff.next() for _ in range(6)] == [30, 60, 120, 240, 300, 300]
    backoff.reset()
    assert backoff.next() == 30


def test_backoff_honours_retry_after() -> None:
    """A longer Retry-After wins, even above the cap; a shorter one doesn't shorten the wait."""
    backoff = Backoff()
    assert backoff.next(retry_after_s=60) == 60
    assert backoff.next(retry_after_s=10) == 60
    assert backoff.next(retry_after_s=900) == 900


def test_fixture_source_loops_and_rewrites_timestamps(clock: FakeClock) -> None:
    """The set replays in order, starts over, and every header says 'now'."""
    expected = load_rt_fixture("rush-hour")
    source = FixtureSource("rush-hour", max_age_s=180, wall=clock.wall)
    seen = []
    for _ in range(len(expected) + 1):
        clock.t += 30
        snapshot = source.fetch()
        seen.append(snapshot)
        assert snapshot.feed_timestamp == clock.wall().replace(microsecond=0)
        assert snapshot.message.header.timestamp == int(clock.wall().timestamp())
    for snapshot, original in zip(seen, [*expected, expected[0]], strict=True):
        assert [e.id for e in snapshot.message.entity] == [e.id for e in original.entity]
    assert seen[0].etag == '"241e46-MmKeTd3wHDWvtubHT8K/bmifuDc"'
    assert seen[0].wire_bytes < seen[0].raw_bytes_len
    # The recorded data itself is untouched between loops.
    assert seen[0].message.entity == seen[-1].message.entity


def test_corrupt_fixture_is_invalid_not_a_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A truncated fixture file is skipped like an invalid live snapshot."""
    folder = tmp_path / "vbb-rt" / "broken"
    folder.mkdir(parents=True)
    (folder / "01.pb.gz").write_bytes(b"\x1f\x8b\x08\x00")
    monkeypatch.setenv("BTP_FIXTURES_DIR", str(tmp_path))
    source = FixtureSource("broken", max_age_s=180)
    with pytest.raises(InvalidSnapshot, match="corrupt gzip"):
        source.fetch()


def test_fixture_source_unknown_set_fails_at_startup() -> None:
    """A typo in BTP_FIXTURE_SET fails before the loop starts."""
    with pytest.raises(FileNotFoundError, match="no-such-set"):
        FixtureSource("no-such-set", max_age_s=180)
