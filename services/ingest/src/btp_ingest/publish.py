"""Publish each snapshot's new, changed and removed trips to the trip-updates topic."""

import logging
import time
from collections.abc import Callable
from datetime import datetime
from typing import Protocol

from confluent_kafka import KafkaError, KafkaException, Message
from confluent_kafka import Producer as KafkaProducer

from btp_ingest.diff import Changes, Fingerprints, diff
from btp_ingest.feed import Snapshot

log = logging.getLogger(__name__)

SCHEMA = b"gtfs-rt.TripUpdate/2.0"

Headers = list[tuple[str, str | bytes | None]]
DeliveryCallback = Callable[[KafkaError | None, Message], None]


class Producer(Protocol):
    """The parts of confluent_kafka.Producer the publisher uses."""

    def produce(
        self,
        topic: str,
        value: str | bytes | None = None,
        key: str | bytes | None = None,
        *,
        on_delivery: DeliveryCallback | None = None,
        headers: Headers | None = None,
    ) -> None:
        """Queue one message."""
        ...

    def poll(self, timeout: float = -1) -> int:
        """Serve delivery callbacks."""
        ...

    def flush(self, timeout: float = -1) -> int:
        """Wait for queued messages; return how many are still queued."""
        ...


def make_producer(bootstrap: str) -> KafkaProducer:
    """Return an idempotent, zstd-compressed producer that waits for all replicas."""
    return KafkaProducer(
        {
            "bootstrap.servers": bootstrap,
            "acks": "all",
            "enable.idempotence": True,
            "compression.type": "zstd",
            "linger.ms": 50,
        }
    )


class Publisher:
    """Diffs each snapshot against the last published one and sends only the changes.

    The fingerprints (and the last header timestamp) are updated only after every message of
    a snapshot was delivered, so a failed publish is retried by the next snapshot's diff.
    """

    def __init__(self, producer: Producer, topic: str, flush_timeout_s: float = 10.0) -> None:
        """Start with no state: the first snapshot publishes every trip as new."""
        self.producer = producer
        self.topic = topic
        self.flush_timeout_s = flush_timeout_s
        self.fingerprints: Fingerprints = {}
        self.last_feed_ts: datetime | None = None
        self._failed = 0  # delivery failures of the snapshot being published

    def handle(self, snapshot: Snapshot) -> bool:
        """Publish the snapshot's changes and log a summary; return True if state was committed.

        Failures are logged, not raised, so the poll loop keeps running.
        """
        if self.last_feed_ts is not None and snapshot.feed_timestamp <= self.last_feed_ts:
            log.warning(
                "snapshot not newer than the last published one, skipped",
                extra={
                    "fields": {
                        "feed_ts": snapshot.feed_timestamp.isoformat(),
                        "last_feed_ts": self.last_feed_ts.isoformat(),
                    }
                },
            )
            return False

        started = time.monotonic()
        changes = diff(self.fingerprints, snapshot.message)
        self._failed = 0
        not_queued = self._send(changes, snapshot)
        queued = self.producer.flush(self.flush_timeout_s)
        failed = self._failed + not_queued
        ok = failed == 0 and queued == 0
        if ok:
            self.fingerprints = changes.fingerprints
            self.last_feed_ts = snapshot.feed_timestamp

        fields: dict[str, object] = {
            "trips": changes.total,
            "new": len(changes.new),
            "changed": len(changes.changed),
            "removed": len(changes.removed),
            "duplicates": changes.duplicates,
            "change_rate_pct": changes.change_rate_pct,
            "publish_ms": round((time.monotonic() - started) * 1000),
            "committed": ok,
        }
        if not ok:
            fields.update(failed=failed, still_queued=queued)
        log.log(logging.INFO if ok else logging.WARNING, "publish", extra={"fields": fields})
        if changes.duplicates:
            log.warning(
                "duplicate trip keys in snapshot, kept the last",
                extra={"fields": {"duplicates": changes.duplicates, "etag": snapshot.etag}},
            )
        return ok

    def _on_delivery(self, err: KafkaError | None, _msg: Message) -> None:
        """Count messages the broker didn't accept."""
        if err is not None:
            self._failed += 1

    def _send(self, changes: Changes, snapshot: Snapshot) -> int:
        """Queue every change; return 1 if the producer refused one (rest skipped), else 0."""
        feed_ts = str(int(snapshot.feed_timestamp.timestamp())).encode()
        snapshot_id = (snapshot.etag or feed_ts.decode()).encode()

        def headers(event: bytes) -> Headers:
            """Return the headers every message carries."""
            return [
                ("btp-feed-ts", feed_ts),
                ("btp-snapshot", snapshot_id),
                ("btp-event", event),
                ("btp-schema", SCHEMA),
            ]

        batches: list[tuple[bytes, list[tuple[str, bytes | None]]]] = [
            (b"new", [(k, tu.SerializeToString()) for k, tu in changes.new]),
            (b"changed", [(k, tu.SerializeToString()) for k, tu in changes.changed]),
            (b"removed", [(k, None) for k in changes.removed]),
        ]
        for event, messages in batches:
            for key, value in messages:
                try:
                    self.producer.produce(
                        self.topic,
                        value,
                        key,
                        on_delivery=self._on_delivery,
                        headers=headers(event),
                    )
                except (BufferError, KafkaException) as exc:
                    log.warning("produce failed: %s", exc)
                    return 1
                self.producer.poll(0)
        return 0
