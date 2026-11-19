"""Tests for the publisher with a fake producer: messages, headers and state commits."""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from confluent_kafka import KafkaError
from google.transit.gtfs_realtime_pb2 import FeedMessage, TripUpdate

from btp_common.fixtures import load_rt_fixture
from btp_ingest.diff import diff
from btp_ingest.feed import Snapshot
from btp_ingest.publish import DeliveryCallback, Headers, Publisher

START = datetime(2026, 11, 18, 17, 3, 41, tzinfo=UTC)


@dataclass
class Sent:
    """One produced message."""

    key: str
    value: bytes | None
    headers: dict[str, bytes]


@dataclass
class FakeProducer:
    """Records messages; fails deliveries or leaves messages queued on request."""

    fail_deliveries: bool = False
    leave_queued: int = 0
    refuse_after: int | None = None
    sent: list[Sent] = field(default_factory=list)
    pending: list[DeliveryCallback] = field(default_factory=list)

    def produce(
        self,
        topic: str,
        value: str | bytes | None = None,
        key: str | bytes | None = None,
        *,
        on_delivery: DeliveryCallback | None = None,
        headers: Headers | None = None,
    ) -> None:
        """Record the message and remember its callback for flush()."""
        if self.refuse_after is not None and len(self.sent) >= self.refuse_after:
            raise BufferError("queue full")
        assert topic == "trip-updates"
        assert isinstance(key, str) and not isinstance(value, str)
        hdrs = {k: v for k, v in headers or [] if isinstance(v, bytes)}
        self.sent.append(Sent(key, value, hdrs))
        if on_delivery is not None:
            self.pending.append(on_delivery)

    def poll(self, timeout: float = -1) -> int:
        """Deliver nothing until flush()."""
        return 0

    def flush(self, timeout: float = -1) -> int:
        """Run the delivery callbacks; return the configured number still queued."""
        err = KafkaError(KafkaError._MSG_TIMED_OUT) if self.fail_deliveries else None
        for callback in self.pending:
            callback(err, None)  # type: ignore[arg-type]  # the publisher ignores the message
        self.pending.clear()
        return self.leave_queued


def _snapshot(message: FeedMessage, at: datetime, etag: str | None = '"abc"') -> Snapshot:
    """Wrap a message as a fetched snapshot."""
    return Snapshot(
        feed_timestamp=at,
        fetched_at=at,
        etag=etag,
        raw_bytes_len=0,
        wire_bytes=0,
        message=message,
    )


def test_publishes_only_changes_with_headers_and_tombstones() -> None:
    """Every new, changed and removed trip is one message with the agreed key and headers."""
    first, second = load_rt_fixture("rush-hour")[:2]
    producer = FakeProducer()
    publisher = Publisher(producer, "trip-updates")
    assert publisher.handle(_snapshot(first, START))
    assert len(producer.sent) == len(publisher.fingerprints)
    assert {s.headers["btp-event"] for s in producer.sent} == {b"new"}

    producer.sent.clear()
    expected = diff(diff({}, first).fingerprints, second)
    assert publisher.handle(_snapshot(second, START + timedelta(seconds=30), etag=None))
    by_event: dict[bytes, list[Sent]] = {}
    for s in producer.sent:
        by_event.setdefault(s.headers["btp-event"], []).append(s)
    assert [s.key for s in by_event.get(b"new", [])] == [k for k, _ in expected.new]
    assert [s.key for s in by_event.get(b"changed", [])] == [k for k, _ in expected.changed]
    assert [s.key for s in by_event.get(b"removed", [])] == expected.removed
    assert all(s.value is None for s in by_event[b"removed"])

    changed_key, changed_tu = expected.changed[0]
    sent = next(s for s in by_event[b"changed"] if s.key == changed_key)
    assert sent.value is not None
    assert TripUpdate.FromString(sent.value) == changed_tu  # timestamp kept as is
    feed_ts = str(int((START + timedelta(seconds=30)).timestamp())).encode()
    assert sent.headers == {
        "btp-feed-ts": feed_ts,
        "btp-snapshot": feed_ts,  # no ETag: the feed timestamp identifies the snapshot
        "btp-event": b"changed",
        "btp-schema": b"gtfs-rt.TripUpdate/2.0",
    }


def test_failed_delivery_keeps_state_and_next_snapshot_retries() -> None:
    """If any message fails, nothing is committed and the next snapshot sends it all again."""
    snapshot = load_rt_fixture("evening")[0]
    producer = FakeProducer(fail_deliveries=True)
    publisher = Publisher(producer, "trip-updates")
    assert not publisher.handle(_snapshot(snapshot, START))
    assert publisher.fingerprints == {} and publisher.last_feed_ts is None
    first_try = len(producer.sent)

    producer.fail_deliveries = False
    producer.sent.clear()
    assert publisher.handle(_snapshot(snapshot, START + timedelta(seconds=30)))
    assert len(producer.sent) == first_try
    assert len(publisher.fingerprints) == first_try


def test_messages_still_queued_after_flush_keep_state() -> None:
    """A flush that times out with messages queued doesn't commit."""
    producer = FakeProducer(leave_queued=3)
    publisher = Publisher(producer, "trip-updates")
    assert not publisher.handle(_snapshot(load_rt_fixture("evening")[0], START))
    assert publisher.fingerprints == {}


def test_refused_produce_keeps_state() -> None:
    """A full local queue (BufferError) stops the snapshot without committing."""
    producer = FakeProducer(refuse_after=5)
    publisher = Publisher(producer, "trip-updates")
    assert not publisher.handle(_snapshot(load_rt_fixture("evening")[0], START))
    assert len(producer.sent) == 5
    assert publisher.fingerprints == {}


def test_snapshot_not_newer_than_the_last_is_skipped() -> None:
    """A header timestamp that goes backwards (or repeats) publishes nothing."""
    first, second = load_rt_fixture("rush-hour")[:2]
    producer = FakeProducer()
    publisher = Publisher(producer, "trip-updates")
    assert publisher.handle(_snapshot(first, START))
    state = publisher.fingerprints
    producer.sent.clear()
    assert not publisher.handle(_snapshot(second, START - timedelta(seconds=30)))
    assert not publisher.handle(_snapshot(second, START))
    assert producer.sent == []
    assert publisher.fingerprints is state
