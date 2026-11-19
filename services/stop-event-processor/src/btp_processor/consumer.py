"""The consume loop and the btp-processor entry point.

At least once: a batch is written to Postgres in one transaction, and its offsets are
committed only after that transaction committed. A crash in between replays the batch,
which the feed_ts guard in store.py makes harmless.
"""

import logging
import signal
import sys
import threading
import time
from collections import Counter
from collections.abc import Sequence
from types import FrameType
from typing import Any, Literal, Protocol

import psycopg
from confluent_kafka import Consumer as KafkaConsumer
from confluent_kafka import KafkaError, KafkaException, Message, TopicPartition
from pydantic import ValidationError

from btp_common.logging import configure_logging
from btp_processor.parse import Headers, Removal, Skipped, Update, parse_message
from btp_processor.settings import ProcessorSettings
from btp_processor.store import Connection, apply_batch, apply_schema

log = logging.getLogger(__name__)

# Topic auto-creation is off: a missing topic won't appear by itself, so exit instead of
# polling forever.
MISSING_TOPIC = (KafkaError.UNKNOWN_TOPIC_OR_PART, KafkaError._UNKNOWN_TOPIC)


class KafkaMessage(Protocol):
    """The parts of confluent_kafka.Message the processor reads."""

    def topic(self) -> str | None:
        """Return the topic."""
        ...

    def partition(self) -> int | None:
        """Return the partition."""
        ...

    def offset(self) -> int | None:
        """Return the offset."""
        ...

    def key(self) -> bytes | None:
        """Return the key."""
        ...

    def value(self) -> bytes | None:
        """Return the value; None for a tombstone."""
        ...

    def headers(self) -> Headers | None:
        """Return the headers."""
        ...

    def error(self) -> KafkaError | None:
        """Return the error, if this is an error event rather than a message."""
        ...


class Consumer(Protocol):
    """The parts of confluent_kafka.Consumer the loop uses."""

    def consume(self, num_messages: int = 1, timeout: float = -1) -> list[Message]:
        """Return up to num_messages, waiting at most timeout seconds."""
        ...

    def commit(
        self, *, offsets: list[TopicPartition], asynchronous: Literal[False]
    ) -> list[TopicPartition]:
        """Commit offsets synchronously."""
        ...

    def assignment(self) -> list[TopicPartition]:
        """Return the assigned partitions."""
        ...

    def position(self, partitions: list[TopicPartition]) -> list[TopicPartition]:
        """Return the next offset to consume per partition."""
        ...

    def get_watermark_offsets(
        self, partition: TopicPartition, timeout: float = -1, cached: bool = False
    ) -> tuple[int, int]:
        """Return the low and high watermark of a partition."""
        ...


class StopSignal(Protocol):
    """The part of threading.Event the loop uses."""

    def is_set(self) -> bool:
        """Return True once shutdown was requested."""
        ...


def make_consumer(settings: ProcessorSettings) -> KafkaConsumer:
    """Return a consumer in the processor's group that never commits on its own."""
    return KafkaConsumer(
        {
            "bootstrap.servers": settings.kafka_bootstrap,
            "group.id": settings.kafka_group,
            "enable.auto.commit": False,
            # A new group starts at the oldest retained message (24 h): replaying is safe.
            "auto.offset.reset": "earliest",
            # librdkafka prefetches up to 64 MB by default; the container has 256 MB.
            "queued.max.messages.kbytes": 16384,
        }
    )


def offsets_to_commit(messages: Sequence[KafkaMessage]) -> list[TopicPartition]:
    """Return, per partition, the offset after the last message of the batch."""
    last: dict[tuple[str, int], int] = {}
    for msg in messages:
        topic, partition, offset = msg.topic(), msg.partition(), msg.offset()
        if topic is None or partition is None or offset is None or offset < 0:
            continue
        key = (topic, partition)
        last[key] = max(last.get(key, -1), offset)
    return [TopicPartition(t, p, o + 1) for (t, p), o in sorted(last.items())]


def lag(consumer: Consumer) -> int | None:
    """Return messages behind the end of the assigned partitions, from cached watermarks.

    None if unknown, e.g. with no partitions assigned during a rebalance.
    """
    total = 0
    try:
        assigned = consumer.assignment()
        if not assigned:
            return None
        for tp in consumer.position(assigned):
            _low, high = consumer.get_watermark_offsets(tp, cached=True)
            if tp.offset < 0 or high < 0:
                return None
            total += max(0, high - tp.offset)
    except KafkaException:
        return None
    return total


def process(conn: Connection, messages: Sequence[KafkaMessage]) -> dict[str, Any]:
    """Parse and store one batch in one transaction; return the counts for the log line."""
    items: list[Update | Removal] = []
    skipped: Counter[str] = Counter()
    stops_without_sequence = 0
    first_invalid: str | None = None
    for msg in messages:
        parsed = parse_message(msg.key(), msg.value(), msg.headers())
        if isinstance(parsed, Skipped):
            skipped[parsed.reason] += 1
            if parsed.reason == "invalid" and first_invalid is None:
                first_invalid = f"{msg.topic()}/{msg.partition()}@{msg.offset()}: {parsed.detail}"
            continue
        if isinstance(parsed, Update):
            stops_without_sequence += parsed.stops_without_sequence
        items.append(parsed)

    result = apply_batch(conn, items)
    fields: dict[str, Any] = {
        "messages": len(messages),
        "trips_upserted": result.trips_written,
        "trips_ignored_older": result.trips_ignored_older,
        "removals_applied": result.removals_applied,
        "removals_ignored": result.removals_ignored,
        "stops_upserted": result.stops_written,
        "stops_ignored_older": result.stops_ignored_older,
        "skipped_no_start_date": skipped["no_start_date"],
        "skipped_no_stop_sequence": stops_without_sequence,
        "skipped_invalid": skipped["invalid"],
    }
    if first_invalid:
        fields["first_invalid"] = first_invalid
    return fields


def run(
    consumer: Consumer,
    conn: Connection,
    stop: StopSignal,
    *,
    max_messages: int = 500,
    max_wait_s: float = 1.0,
) -> None:
    """Consume batches until stop is set: store each, then commit its offsets.

    Message errors that aren't fatal are logged and skipped. A fatal Kafka error, a missing
    topic or a database error raises and ends the loop without committing. A commit that
    fails without a fatal error (e.g. the partitions were revoked in a rebalance) is logged:
    the batch is consumed again, which the feed_ts guard makes harmless.
    """
    while not stop.is_set():
        batch = consumer.consume(num_messages=max_messages, timeout=max_wait_s)
        messages: list[KafkaMessage] = []
        for msg in batch:
            err = msg.error()
            if err is None:
                messages.append(msg)
            elif err.fatal() or err.code() in MISSING_TOPIC:
                raise KafkaException(err)
            else:
                log.warning("consumer error: %s", err)
        if not messages:
            continue
        started = time.monotonic()
        fields = process(conn, messages)
        # Only after the transaction committed: a crash before this line replays the batch.
        try:
            consumer.commit(offsets=offsets_to_commit(messages), asynchronous=False)
            fields["committed"] = True
        except KafkaException as exc:
            if exc.args[0].fatal():
                raise
            fields["committed"] = False
            fields["commit_error"] = str(exc)
        fields["lag"] = lag(consumer)
        fields["batch_ms"] = round((time.monotonic() - started) * 1000)
        warn = fields["skipped_invalid"] or not fields["committed"]
        level = logging.WARNING if warn else logging.INFO
        log.log(level, "batch", extra={"fields": fields})


def connect(settings: ProcessorSettings) -> psycopg.Connection[Any]:
    """Open an autocommit connection as the processor role (transactions are explicit)."""
    return psycopg.connect(
        host=settings.pg_host,
        port=settings.pg_port,
        dbname=settings.pg_db,
        user=settings.pg_user,
        password=settings.pg_processor_password,
        autocommit=True,
        connect_timeout=10,
    )


def main() -> int:
    """Run the processor until SIGTERM or SIGINT; return the exit code."""
    try:
        settings = ProcessorSettings()
    except ValidationError as exc:
        print(f"btp-processor: invalid settings:\n{exc}", file=sys.stderr)
        return 2
    configure_logging(settings)

    stop = threading.Event()

    def _request_stop(signum: int, _frame: FrameType | None) -> None:
        """Ask the loop to finish its current batch and exit."""
        log.info("%s received, stopping", signal.Signals(signum).name)
        stop.set()

    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)

    try:
        with connect(settings) as conn:
            apply_schema(conn)
            consumer = make_consumer(settings)
            try:
                consumer.subscribe([settings.kafka_topic])
                log.info(
                    "starting",
                    extra={
                        "fields": {
                            "topic": settings.kafka_topic,
                            "group": settings.kafka_group,
                            "max_messages": settings.batch_max_messages,
                            "max_wait_s": settings.batch_max_wait_s,
                        }
                    },
                )
                run(
                    consumer,
                    conn,
                    stop,
                    max_messages=settings.batch_max_messages,
                    max_wait_s=settings.batch_max_wait_s,
                )
            finally:
                consumer.close()
    except (KafkaException, psycopg.Error) as exc:
        # Exit so the container restarts; uncommitted offsets are consumed again.
        log.error("fatal error, exiting: %s", exc)
        return 1
    log.info("stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
