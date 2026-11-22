"""Tests for the raw snapshot archive: keys, interval rule, worker, shutdown, S3 call."""

import gzip
import logging
import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta, timezone

import boto3
import pytest
from botocore.stub import Stubber
from google.transit.gtfs_realtime_pb2 import FeedMessage

from btp_ingest.feed import Snapshot
from btp_ingest.raw import RawArchiver, S3Uploader, gzip_body, raw_key

PREFIX = "raw/vbb-rt/"
T0 = datetime(2026, 11, 22, 17, 3, 41, tzinfo=UTC)
WAIT_S = 5.0  # upper bound for thread hand-offs; tests finish in milliseconds


def snap(feed_timestamp: datetime = T0, wire: bytes | None = None, entities: int = 2) -> Snapshot:
    """A live-like snapshot whose wire bytes are gzip."""
    message = FeedMessage()
    message.header.gtfs_realtime_version = "2.0"
    message.header.timestamp = int(feed_timestamp.timestamp())
    for i in range(entities):
        message.entity.add(id=str(i))
    data = message.SerializeToString()
    return Snapshot(
        feed_timestamp=feed_timestamp,
        fetched_at=feed_timestamp,
        etag='"v1"',
        raw_bytes_len=len(data),
        wire_bytes=0,
        message=message,
        wire=gzip.compress(data) if wire is None else wire,
        wire_gzip=wire is None,
    )


class FakeUploader:
    """Records uploads; can fail, or block until released, to simulate a slow S3."""

    def __init__(self, *, fail: int = 0, block: bool = False) -> None:
        """Fail the first `fail` uploads; with block, each upload waits for release()."""
        self.fail = fail
        self.calls: list[tuple[str, bytes, dict[str, str]]] = []
        self.started = threading.Event()
        self._release = threading.Event()
        if not block:
            self._release.set()

    def put(self, key: str, body: bytes, metadata: dict[str, str]) -> None:
        """Record the call, wait if blocking, and fail if asked to."""
        self.started.set()
        self._release.wait(WAIT_S)
        self.calls.append((key, body, metadata))
        if self.fail:
            self.fail -= 1
            raise ConnectionError("S3 unreachable")

    def release(self) -> None:
        """Let blocked uploads finish."""
        self._release.set()


def archive_and_wait(archiver: RawArchiver, snapshot: Snapshot) -> str:
    """Offer a snapshot and wait until a queued upload has finished."""
    result = archiver.maybe_archive(snapshot)
    archiver.wait_idle()
    return result


@pytest.fixture
def uploader() -> FakeUploader:
    """A fast, successful uploader."""
    return FakeUploader()


@pytest.fixture
def archiver(uploader: FakeUploader) -> Iterator[RawArchiver]:
    """An archiver with the default 300 s interval."""
    a = RawArchiver(uploader, PREFIX, 300.0)
    yield a
    a.close(1.0)


@pytest.mark.parametrize(
    ("ts", "key"),
    [
        (T0, "raw/vbb-rt/date=2026-11-22/hour=17/20261122T170341Z.pb.gz"),
        (
            datetime(2026, 11, 22, 23, 59, 59, tzinfo=UTC),
            "raw/vbb-rt/date=2026-11-22/hour=23/20261122T235959Z.pb.gz",
        ),
        (
            datetime(2026, 11, 23, 0, 0, 0, tzinfo=UTC),
            "raw/vbb-rt/date=2026-11-23/hour=00/20261123T000000Z.pb.gz",
        ),
        # 00:30 Berlin summer time on 30 March is still 29 March in UTC.
        (
            datetime(2026, 3, 30, 0, 30, tzinfo=timezone(timedelta(hours=2))),
            "raw/vbb-rt/date=2026-03-29/hour=22/20260329T223000Z.pb.gz",
        ),
    ],
)
def test_key_uses_utc_hive_partitions(ts: datetime, key: str) -> None:
    """Date and hour partitions and the file name come from the header time in UTC."""
    assert raw_key(PREFIX, ts) == key


def test_both_hours_of_the_dst_fall_back_get_distinct_keys() -> None:
    """02:30 Berlin happens twice on 25 October; UTC keys keep both."""
    first = datetime(2026, 10, 25, 0, 30, tzinfo=UTC)  # 02:30 CEST
    second = first + timedelta(hours=1)  # 02:30 CET
    assert raw_key(PREFIX, first) != raw_key(PREFIX, second)


def test_gzip_wire_bytes_are_uploaded_unchanged() -> None:
    """A gzip body from VBB is stored byte for byte."""
    s = snap()
    assert gzip_body(s) == s.wire


def test_plain_wire_bytes_are_gzipped() -> None:
    """A body sent without gzip is compressed and decompresses to the original."""
    s = snap(wire=b"plain protobuf bytes")
    assert gzip.decompress(gzip_body(s)) == b"plain protobuf bytes"


def test_first_uploads_then_waits_for_the_interval(
    archiver: RawArchiver, uploader: FakeUploader
) -> None:
    """First snapshot uploads; nothing until 300 s of header time later; then the next."""
    assert archive_and_wait(archiver, snap(T0)) == "queued"
    assert archive_and_wait(archiver, snap(T0 + timedelta(seconds=30))) == "not_due"
    assert archive_and_wait(archiver, snap(T0 + timedelta(seconds=299))) == "not_due"
    assert archive_and_wait(archiver, snap(T0 + timedelta(seconds=300))) == "queued"
    assert [c[0] for c in uploader.calls] == [
        raw_key(PREFIX, T0),
        raw_key(PREFIX, T0 + timedelta(seconds=300)),
    ]


def test_header_time_going_back_does_not_upload(
    archiver: RawArchiver, uploader: FakeUploader
) -> None:
    """A snapshot older than the last upload isn't due."""
    archive_and_wait(archiver, snap(T0))
    assert archive_and_wait(archiver, snap(T0 - timedelta(hours=1))) == "not_due"


def test_object_metadata(archiver: RawArchiver, uploader: FakeUploader) -> None:
    """Metadata carries the header time, ETag and entity count."""
    archive_and_wait(archiver, snap(T0, entities=3))
    _, _, metadata = uploader.calls[0]
    assert metadata == {"feed-ts": "2026-11-22T17:03:41+00:00", "etag": '"v1"', "entity-count": "3"}


def test_failure_keeps_last_success_and_retries(caplog: pytest.LogCaptureFixture) -> None:
    """A failed upload is logged and counted; the very next snapshot tries again."""
    caplog.set_level(logging.INFO)
    uploader = FakeUploader(fail=1)
    archiver = RawArchiver(uploader, PREFIX, 300.0)
    try:
        assert archive_and_wait(archiver, snap(T0)) == "queued"
        assert archiver.failed == 1
        assert archiver.last_success is None
        later = T0 + timedelta(seconds=30)
        assert archive_and_wait(archiver, snap(later)) == "queued"
        assert archiver.last_success == later
    finally:
        archiver.close(1.0)
    lines = [r for r in caplog.records if r.getMessage() == "raw upload"]
    assert [r.levelno for r in lines] == [logging.WARNING, logging.INFO]
    failed = lines[0].fields  # type: ignore[attr-defined]
    assert failed["ok"] is False
    assert failed["raw_failed"] == 1
    assert "S3 unreachable" in failed["error"]
    assert lines[1].fields["raw_bytes"] > 0  # type: ignore[attr-defined]


def test_busy_upload_skips_without_blocking() -> None:
    """While an upload runs, due snapshots are skipped at once and counted."""
    uploader = FakeUploader(block=True)
    archiver = RawArchiver(uploader, PREFIX, 300.0)
    try:
        assert archiver.maybe_archive(snap(T0)) == "queued"
        assert uploader.started.wait(WAIT_S)
        # Still due (no success yet) but busy: returns immediately, nothing queued.
        assert archiver.maybe_archive(snap(T0 + timedelta(seconds=30))) == "skipped_busy"
        assert archiver.maybe_archive(snap(T0 + timedelta(seconds=60))) == "skipped_busy"
        assert archiver.skipped_busy == 2
        uploader.release()
    finally:
        assert archiver.close(WAIT_S)
    assert len(uploader.calls) == 1


def test_close_waits_for_a_running_upload() -> None:
    """On shutdown, an upload in progress is allowed to finish."""
    uploader = FakeUploader(block=True)
    archiver = RawArchiver(uploader, PREFIX, 300.0)
    archiver.maybe_archive(snap(T0))
    assert uploader.started.wait(WAIT_S)
    threading.Timer(0.05, uploader.release).start()
    assert archiver.close(WAIT_S) is True
    assert len(uploader.calls) == 1
    assert archiver.maybe_archive(snap(T0 + timedelta(hours=1))) == "not_due"


def test_close_gives_up_after_the_timeout(caplog: pytest.LogCaptureFixture) -> None:
    """A hung upload doesn't hold the shutdown beyond the timeout."""
    uploader = FakeUploader(block=True)
    archiver = RawArchiver(uploader, PREFIX, 300.0)
    archiver.maybe_archive(snap(T0))
    assert uploader.started.wait(WAIT_S)
    assert archiver.close(0.05) is False
    assert "still running" in caplog.text
    uploader.release()


def test_s3_uploader_sends_type_encoding_and_metadata() -> None:
    """The boto3 call has the content type, gzip encoding and metadata."""
    client = boto3.client("s3", region_name="eu-north-1")
    metadata = {"feed-ts": "2026-11-22T17:03:41+00:00", "etag": '"v1"', "entity-count": "3"}
    with Stubber(client) as stubber:
        stubber.add_response(
            "put_object",
            {},
            {
                "Bucket": "btp-data-test",
                "Key": "raw/vbb-rt/x.pb.gz",
                "Body": b"gz",
                "ContentType": "application/x-protobuf",
                "ContentEncoding": "gzip",
                "Metadata": metadata,
            },
        )
        S3Uploader("btp-data-test", client).put("raw/vbb-rt/x.pb.gz", b"gz", metadata)
        stubber.assert_no_pending_responses()
