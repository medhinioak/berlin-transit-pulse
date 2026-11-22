"""Archive one raw feed snapshot to S3 every few minutes, in a background thread (ADR-0008)."""

import gzip
import logging
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal, Protocol

from btp_ingest.feed import Snapshot

if TYPE_CHECKING:
    from types_boto3_s3 import S3Client

log = logging.getLogger(__name__)

CONTENT_TYPE = "application/x-protobuf"
GZIP_LEVEL = 6
CLOSE_TIMEOUT_S = 10.0

ArchiveResult = Literal["queued", "skipped_busy", "not_due"]


def raw_key(prefix: str, feed_timestamp: datetime) -> str:
    """Return the object key for a snapshot: Hive-style date and hour partitions, in UTC."""
    ts = feed_timestamp.astimezone(UTC)
    return f"{prefix}date={ts:%Y-%m-%d}/hour={ts:%H}/{ts:%Y%m%dT%H%M%SZ}.pb.gz"


def gzip_body(snapshot: Snapshot) -> bytes:
    """Return the snapshot body as gzip: the wire bytes unchanged if they already are."""
    if snapshot.wire_gzip:
        return snapshot.wire
    return gzip.compress(snapshot.wire, compresslevel=GZIP_LEVEL)


class Uploader(Protocol):
    """Stores one object."""

    def put(self, key: str, body: bytes, metadata: dict[str, str]) -> None:
        """Upload body under key; raise on failure."""
        ...


class S3Uploader:
    """Uploads gzipped protobuf objects to one bucket with boto3."""

    def __init__(self, bucket: str, client: "S3Client | None" = None) -> None:
        """Use the given client, or one with standard retries and short timeouts."""
        # Imported here: boto3 adds about 30 MB, which fixtures mode and local runs don't need.
        import boto3
        from botocore.config import Config

        self.bucket = bucket
        self._client = client or boto3.client(
            "s3",
            config=Config(retries={"mode": "standard"}, connect_timeout=10, read_timeout=30),
        )

    def put(self, key: str, body: bytes, metadata: dict[str, str]) -> None:
        """Upload one snapshot with its content type, encoding and metadata."""
        self._client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=body,
            ContentType=CONTENT_TYPE,
            ContentEncoding="gzip",
            Metadata=metadata,
        )


@dataclass(frozen=True)
class _Job:
    """One upload, holding only bytes and metadata (never the FeedMessage)."""

    key: str
    body: bytes
    metadata: dict[str, str]
    feed_timestamp: datetime


class RawArchiver:
    """Uploads a snapshot when interval_s passed since the last success; never blocks polling."""

    def __init__(
        self,
        uploader: Uploader,
        prefix: str,
        interval_s: float,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Start the single worker thread."""
        self.uploader = uploader
        self.prefix = prefix
        self.interval_s = interval_s
        self.failed = 0
        self.skipped_busy = 0
        self._clock = clock
        self._lock = threading.Lock()
        self._last_success: datetime | None = None
        self._busy = False
        self._closed = False
        self._jobs: queue.Queue[_Job | None] = queue.Queue(maxsize=1)
        self._worker = threading.Thread(target=self._work, name="raw-archiver", daemon=True)
        self._worker.start()

    @property
    def last_success(self) -> datetime | None:
        """Return the feed timestamp of the last successful upload."""
        with self._lock:
            return self._last_success

    def maybe_archive(self, snapshot: Snapshot) -> ArchiveResult:
        """Queue the snapshot if it's due and the worker is idle; return what happened."""
        with self._lock:
            if self._closed or not self._due(snapshot.feed_timestamp):
                return "not_due"
            if self._busy:
                self.skipped_busy += 1
                log.warning(
                    "raw upload skipped: previous upload still running",
                    extra={"fields": {"raw_skipped_busy": self.skipped_busy}},
                )
                return "skipped_busy"
            self._busy = True
        job = _Job(
            key=raw_key(self.prefix, snapshot.feed_timestamp),
            body=gzip_body(snapshot),
            metadata={
                "feed-ts": snapshot.feed_timestamp.astimezone(UTC).isoformat(),
                "etag": snapshot.etag or "",
                "entity-count": str(len(snapshot.message.entity)),
            },
            feed_timestamp=snapshot.feed_timestamp,
        )
        self._jobs.put_nowait(job)
        return "queued"

    def close(self, timeout_s: float = CLOSE_TIMEOUT_S) -> bool:
        """Stop accepting snapshots, wait up to timeout_s for a running upload; True if done."""
        deadline = time.monotonic() + timeout_s
        with self._lock:
            self._closed = True
        try:
            self._jobs.put(None, timeout=timeout_s)
        except queue.Full:
            pass
        self._worker.join(max(0.0, deadline - time.monotonic()))
        finished = not self._worker.is_alive()
        if not finished:
            log.warning("raw upload still running after %.0f s, exiting anyway", timeout_s)
        return finished

    def wait_idle(self) -> None:
        """Block until every queued upload has finished and its result is recorded."""
        self._jobs.join()

    def _due(self, feed_timestamp: datetime) -> bool:
        """Return True if no upload succeeded yet or interval_s passed since the last one."""
        if self._last_success is None:
            return True
        return (feed_timestamp - self._last_success).total_seconds() >= self.interval_s

    def _work(self) -> None:
        """Upload queued jobs one at a time until close() sends None."""
        while (job := self._jobs.get()) is not None:
            self._upload(job)
            self._jobs.task_done()

    def _upload(self, job: _Job) -> None:
        """Run one upload, log one line and update the counters."""
        started = self._clock()
        error: Exception | None = None
        try:
            self.uploader.put(job.key, job.body, job.metadata)
        except Exception as exc:  # any failure is logged and counted, never fatal
            error = exc
        with self._lock:
            if error is None:
                self._last_success = job.feed_timestamp
            else:
                self.failed += 1
            self._busy = False
            fields: dict[str, object] = {
                "raw_key": job.key,
                "raw_bytes": len(job.body),
                "duration_ms": round((self._clock() - started) * 1000),
                "ok": error is None,
                "raw_failed": self.failed,
                "raw_skipped_busy": self.skipped_busy,
            }
        if error is None:
            log.info("raw upload", extra={"fields": fields})
        else:
            fields["error"] = f"{type(error).__name__}: {error}"
            log.warning("raw upload", extra={"fields": fields})
