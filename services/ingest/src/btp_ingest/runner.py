"""The poll loop and the btp-ingest entry point."""

import logging
import signal
import sys
import threading
import time
from collections.abc import Callable
from datetime import datetime
from types import FrameType
from typing import Protocol

from pydantic import ValidationError

from btp_common.logging import configure_logging
from btp_ingest.feed import (
    Backoff,
    FeedClient,
    FeedUnavailable,
    FixtureSource,
    InvalidSnapshot,
    Snapshot,
    now_utc,
)
from btp_ingest.publish import ProducerFatalError, Publisher, make_producer
from btp_ingest.settings import IngestSettings

log = logging.getLogger(__name__)

Handler = Callable[[Snapshot], object]


class Source(Protocol):
    """Anything that returns the next snapshot, or None when the feed hasn't changed."""

    def fetch(self) -> Snapshot | None:
        """Return the next snapshot or None."""
        ...

    def close(self) -> None:
        """Release resources."""
        ...


class StopSignal(Protocol):
    """The parts of threading.Event the loop uses."""

    def is_set(self) -> bool:
        """Return True once shutdown was requested."""
        ...

    def wait(self, timeout: float | None = None) -> bool:
        """Sleep up to timeout, returning early (True) if shutdown is requested."""
        ...


def run(
    source: Source,
    handler: Handler,
    interval_s: float,
    stop: StopSignal,
    *,
    clock: Callable[[], float] = time.monotonic,
    wall: Callable[[], datetime] = now_utc,
) -> None:
    """Poll source every interval_s (start to start) until stop is set, one log line per poll.

    429, 5xx and network errors back off exponentially (Backoff); invalid snapshots are
    counted and skipped. Exceptions from the handler end the loop, so handlers log and
    swallow expected failures (Publisher does) and only bugs stop the service.
    """
    backoff = Backoff()
    invalid_total = 0
    while not stop.is_set():
        started = clock()
        delay = interval_s
        level = logging.INFO
        fields: dict[str, object] = {}
        snapshot: Snapshot | None = None
        try:
            snapshot = source.fetch()
        except FeedUnavailable as exc:
            delay = backoff.next(exc.retry_after_s)
            level = logging.WARNING
            fields.update(status=exc.status, retry_after_s=exc.retry_after_s, next_poll_s=delay)
            if exc.detail:
                fields["reason"] = exc.detail
        except InvalidSnapshot as exc:
            invalid_total += 1
            backoff.reset()
            level = logging.WARNING
            fields.update(status="invalid", reason=str(exc))
        else:
            backoff.reset()
            if snapshot is None:
                fields.update(status="304", wire_bytes=0)
            else:
                age_s = (snapshot.fetched_at - snapshot.feed_timestamp).total_seconds()
                fields.update(
                    status="200",
                    wire_bytes=snapshot.wire_bytes,
                    raw_bytes=snapshot.raw_bytes_len,
                    entities=len(snapshot.message.entity),
                    header_age_s=round(age_s, 1),
                    etag=snapshot.etag,
                )
        fields["duration_ms"] = round((clock() - started) * 1000)
        fields["invalid_total"] = invalid_total
        log.log(level, "poll", extra={"fields": fields})
        if snapshot is not None:
            handler(snapshot)
        stop.wait(max(0.0, started + delay - clock()))


def build_source(settings: IngestSettings) -> FeedClient | FixtureSource:
    """Return the fixture replayer or the live client; raises if the config is unusable."""
    if settings.feed_mode == "fixtures":
        return FixtureSource(settings.fixture_set, settings.feed_max_age_s)
    return FeedClient(settings.feed_url, settings.feed_contact, settings.feed_max_age_s)


def main() -> int:
    """Run the ingest loop until SIGTERM or SIGINT; return the exit code."""
    try:
        settings = IngestSettings()
    except ValidationError as exc:
        print(f"btp-ingest: invalid settings:\n{exc}", file=sys.stderr)
        return 2
    configure_logging(settings)
    # httpx logs every request at INFO; the poll line already covers it.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        source = build_source(settings)
    except (ValueError, FileNotFoundError) as exc:
        log.error("cannot start: %s", exc)
        return 2

    stop = threading.Event()

    def _request_stop(signum: int, _frame: FrameType | None) -> None:
        """Ask the loop to finish its current poll and exit."""
        log.info("%s received, stopping", signal.Signals(signum).name)
        stop.set()

    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)
    target = settings.fixture_set if settings.feed_mode == "fixtures" else settings.feed_url
    log.info(
        "starting",
        extra={
            "fields": {
                "mode": settings.feed_mode,
                "source": target,
                "interval_s": settings.poll_interval_s,
            }
        },
    )
    producer = make_producer(settings.kafka_bootstrap, settings.publish_timeout_s)
    publisher = Publisher(producer, settings.kafka_topic, settings.publish_timeout_s)
    try:
        # RateGuard lives in one process: after a crash and restart, wait a full interval
        # before the first live request so a restart loop can't exceed VBB's rate limit.
        if settings.feed_mode == "fixtures" or not stop.wait(settings.poll_interval_s):
            run(source, publisher.handle, settings.poll_interval_s, stop)
    except ProducerFatalError as exc:
        # Exit so the container restarts with a new producer.
        log.error("fatal Kafka producer error, exiting: %s", exc)
        return 1
    finally:
        source.close()
        producer.flush(settings.publish_timeout_s)
    log.info("stopped")
    return 0
