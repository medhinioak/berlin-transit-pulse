"""Integration tests: idempotency, ordering guard, tombstones and crash-restart.

Needs `make up`. Runs as btp_processor against the compose Postgres and Kafka (override with
BTP_PG_HOST, BTP_PG_PORT, BTP_PG_PROCESSOR_PASSWORD, BTP_KAFKA_BOOTSTRAP). Every test writes
trips with its own trip_id prefix and deletes them afterwards, so the compose processor
writing the same tables doesn't interfere. Messages are built from the rush-hour fixtures the
way ingest publishes them (schemas/trip-updates.md).
"""

import os
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import psycopg
import pytest
from confluent_kafka import Consumer as KafkaConsumer
from confluent_kafka import Message, Producer, TopicPartition
from confluent_kafka.admin import AdminClient, NewTopic
from google.transit.gtfs_realtime_pb2 import TripUpdate

from btp_common.fixtures import load_rt_fixture
from btp_processor.consumer import process, run
from btp_processor.settings import ProcessorSettings
from btp_processor.store import apply_schema

pytestmark = pytest.mark.integration

START = datetime(2026, 11, 18, 17, 3, 41, tzinfo=UTC)
Connection = psycopg.Connection[Any]


@dataclass
class Msg:
    """A trip-updates message held in memory, shaped like confluent_kafka.Message."""

    _key: bytes
    _value: bytes | None
    feed_ts: int
    _offset: int = 0

    def key(self) -> bytes:
        """Return the key."""
        return self._key

    def value(self) -> bytes | None:
        """Return the value."""
        return self._value

    def headers(self) -> list[tuple[str, str | bytes | None]]:
        """Return the headers ingest sets."""
        event = b"removed" if self._value is None else b"changed"
        return [
            ("btp-feed-ts", str(self.feed_ts).encode()),
            ("btp-snapshot", str(self.feed_ts).encode()),
            ("btp-event", event),
            ("btp-schema", b"gtfs-rt.TripUpdate/2.0"),
        ]

    def topic(self) -> str:
        """Return a topic name."""
        return "in-memory"

    def partition(self) -> int:
        """Return partition 0."""
        return 0

    def offset(self) -> int:
        """Return the position in the list."""
        return self._offset

    def error(self) -> None:
        """Return no error."""
        return None


def fixture_messages(prefix: str) -> list[Msg]:
    """Return the rush-hour set as published: every trip per snapshot, then tombstones.

    Trip IDs get the prefix. Unlike ingest, unchanged trips are sent again, which only adds
    duplicates the processor must absorb anyway.
    """
    messages: list[Msg] = []
    previous: set[bytes] = set()
    for i, snapshot in enumerate(load_rt_fixture("rush-hour")):
        feed_ts = int((START + timedelta(seconds=30 * i)).timestamp())
        keys: set[bytes] = set()
        for entity in snapshot.entity:
            tu = TripUpdate()
            tu.CopyFrom(entity.trip_update)
            tu.trip.trip_id = prefix + tu.trip.trip_id
            key = f"{tu.trip.trip_id}|{tu.trip.start_date}".encode()
            keys.add(key)
            messages.append(Msg(key, tu.SerializeToString(), feed_ts))
        messages += [Msg(k, None, feed_ts) for k in sorted(previous - keys)]
        previous = keys
    for offset, msg in enumerate(messages):
        msg._offset = offset
    return messages


@pytest.fixture
def conn() -> Iterator[Connection]:
    """Connect as btp_processor and make sure the tables exist."""
    settings = ProcessorSettings()
    with psycopg.connect(
        host=settings.pg_host,
        port=settings.pg_port,
        dbname=settings.pg_db,
        user=settings.pg_user,
        password=settings.pg_processor_password,
        autocommit=True,
    ) as c:
        apply_schema(c)
        yield c


@pytest.fixture
def prefixes(conn: Connection) -> Iterator[list[str]]:
    """Hand out unique trip_id prefixes and delete their rows afterwards."""
    used: list[str] = []
    yield used
    for prefix in used:
        for table in ("trip_state", "stop_predictions"):
            conn.execute(
                f"DELETE FROM events.{table} WHERE starts_with(trip_id, %s)",
                (prefix,),
            )


def new_prefix(prefixes: list[str]) -> str:
    """Return a fresh prefix and register it for cleanup."""
    prefix = f"it-{uuid.uuid4().hex[:8]}-"
    prefixes.append(prefix)
    return prefix


def table_digest(conn: Connection, prefix: str) -> dict[str, tuple[int, str]]:
    """Return row count and md5 of all columns but updated_at, per table, prefix stripped."""
    digest = {}
    for table, order in (
        ("trip_state", "trip_id, service_date"),
        ("stop_predictions", "trip_id, service_date, stop_sequence"),
    ):
        row = conn.execute(
            f"""
            SELECT count(*), coalesce(md5(string_agg(
                (to_jsonb(t) - 'updated_at' - 'trip_id')::text || substr(trip_id, %(n)s),
                '|' ORDER BY {order})), '')
            FROM events.{table} t WHERE starts_with(trip_id, %(p)s)
            """,
            {"p": prefix, "n": len(prefix) + 1},
        ).fetchone()
        assert row is not None
        digest[table] = (row[0], row[1])
    return digest


def trip(conn: Connection, trip_id: str) -> dict[str, Any]:
    """Return the trip_state row of trip_id as a dict."""
    with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
        row = cur.execute(
            "SELECT * FROM events.trip_state WHERE trip_id = %s", (trip_id,)
        ).fetchone()
    assert row is not None
    return row


def test_replay_gives_identical_tables(conn: Connection, prefixes: list[str]) -> None:
    """Replaying the set, and replaying it in single-message batches, changes nothing."""
    prefix = new_prefix(prefixes)
    messages = fixture_messages(prefix)
    first = process(conn, messages)
    expected = table_digest(conn, prefix)
    assert expected["trip_state"][0] > 0 and expected["stop_predictions"][0] > 0
    assert first["skipped_no_stop_sequence"] > 0  # the fixtures have stop_id-only updates

    process(conn, messages)
    assert table_digest(conn, prefix) == expected

    for msg in messages:
        process(conn, [msg])
    assert table_digest(conn, prefix) == expected

    # A fresh run with one-message batches ends in the same state as one big batch.
    other = new_prefix(prefixes)
    for msg in fixture_messages(other):
        process(conn, [msg])
    assert table_digest(conn, other) == expected


def test_older_message_does_not_overwrite(conn: Connection, prefixes: list[str]) -> None:
    """A snapshot delivered after a newer one leaves trips and stops at the newer state."""
    prefix = new_prefix(prefixes)
    messages = [m for m in fixture_messages(prefix) if m.value() is not None]
    by_ts: dict[int, list[Msg]] = {}
    for m in messages:
        by_ts.setdefault(m.feed_ts, []).append(m)
    first_ts, *_, last_ts = sorted(by_ts)

    process(conn, by_ts[last_ts])
    expected = table_digest(conn, prefix)
    result = process(conn, by_ts[first_ts])
    after = table_digest(conn, prefix)

    # Trips in both snapshots are untouched; trips only in the old one are added.
    in_last = {m.key() for m in by_ts[last_ts]}
    both = sum(m.key() in in_last for m in by_ts[first_ts])
    assert result["trips_ignored_older"] == both > 0
    assert after["trip_state"][0] == expected["trip_state"][0] + len(by_ts[first_ts]) - both
    assert result["stops_ignored_older"] > 0
    sample = next(m for m in by_ts[last_ts] if m.key() in {x.key() for x in by_ts[first_ts]})
    trip_id = sample.key().decode().rpartition("|")[0]
    assert trip(conn, trip_id)["feed_ts"] == datetime.fromtimestamp(last_ts, tz=UTC)


def test_tombstone_sets_removed_at(conn: Connection, prefixes: list[str]) -> None:
    """A tombstone marks the trip; an older update can't undo that, a newer one can."""
    prefix = new_prefix(prefixes)
    update = next(m for m in fixture_messages(prefix) if m.value() is not None)
    trip_id = update.key().decode().rpartition("|")[0]
    t = update.feed_ts

    process(conn, [update])
    assert trip(conn, trip_id)["removed_at"] is None
    stops_before = table_digest(conn, prefix)["stop_predictions"]

    result = process(conn, [Msg(update.key(), None, t + 30)])
    assert result["removals_applied"] == 1
    row = trip(conn, trip_id)
    assert row["removed_at"] == datetime.fromtimestamp(t + 30, tz=UTC)
    assert row["route_id"] is not None  # the last known state is kept
    assert table_digest(conn, prefix)["stop_predictions"] == stops_before

    # A late delivery of the update the tombstone superseded doesn't bring the trip back.
    assert process(conn, [update])["trips_ignored_older"] == 1
    assert trip(conn, trip_id)["removed_at"] is not None

    # The trip reappearing in a newer snapshot clears removed_at.
    process(conn, [Msg(update.key(), update.value(), t + 60)])
    assert trip(conn, trip_id)["removed_at"] is None

    # A tombstone for a trip that was never stored changes nothing.
    unknown = f"{prefix}never-seen|20261118".encode()
    assert process(conn, [Msg(unknown, None, t)])["removals_ignored"] == 1


# --- Kafka: crash after the database commit, before the offset commit ---------------------

BOOTSTRAP = os.environ.get("BTP_KAFKA_BOOTSTRAP", "127.0.0.1:9094")


class Crash(Exception):
    """Stands in for the process dying."""


class Harness:
    """Wraps a real consumer: can crash on the nth commit, and stops the loop when idle."""

    def __init__(self, consumer: KafkaConsumer, crash_on_commit: int | None = None) -> None:
        """Wrap consumer; crash_on_commit=n raises Crash instead of the nth commit."""
        self.inner = consumer
        self.crash_on_commit = crash_on_commit
        self.commits = 0
        self.seen = 0
        self.idle = 0
        self.deadline = time.monotonic() + 60

    def consume(self, num_messages: int = 1, timeout: float = -1) -> list[Message]:
        """Consume and track idleness."""
        batch = self.inner.consume(num_messages=num_messages, timeout=timeout)
        self.seen += len(batch)
        self.idle = 0 if batch else self.idle + 1
        return batch

    def commit(
        self, *, offsets: list[TopicPartition], asynchronous: Literal[False]
    ) -> list[TopicPartition]:
        """Commit, unless this is the commit to crash on."""
        self.commits += 1
        if self.commits == self.crash_on_commit:
            raise Crash
        return self.inner.commit(offsets=offsets, asynchronous=asynchronous)

    def assignment(self) -> list[TopicPartition]:
        """Delegate."""
        return self.inner.assignment()

    def position(self, partitions: list[TopicPartition]) -> list[TopicPartition]:
        """Delegate."""
        return self.inner.position(partitions)

    def get_watermark_offsets(
        self, partition: TopicPartition, timeout: float = -1, cached: bool = False
    ) -> tuple[int, int]:
        """Delegate."""
        return self.inner.get_watermark_offsets(partition, timeout, cached)

    def is_set(self) -> bool:
        """Stop after 3 empty polls once messages were seen, or after 60 s."""
        assert time.monotonic() < self.deadline, "consumer didn't finish within 60 s"
        return self.seen > 0 and self.idle >= 3


@pytest.fixture
def topic() -> Iterator[str]:
    """Create a 6-partition test topic and delete it afterwards."""
    admin = AdminClient({"bootstrap.servers": BOOTSTRAP})
    name = f"it-processor-{uuid.uuid4().hex[:8]}"
    admin.create_topics([NewTopic(name, num_partitions=6, replication_factor=1)])[name].result(
        timeout=15
    )
    yield name
    admin.delete_topics([name])[name].result(timeout=15)


def consumer(group: str) -> KafkaConsumer:
    """Return a consumer configured like the processor's, in its own group."""
    return KafkaConsumer(
        {
            "bootstrap.servers": BOOTSTRAP,
            "group.id": group,
            "enable.auto.commit": False,
            "auto.offset.reset": "earliest",
        }
    )


def test_crash_between_db_commit_and_offset_commit(
    conn: Connection, prefixes: list[str], topic: str
) -> None:
    """Dying after the DB commit replays the batch on restart: same final state, no dupes."""
    reference = new_prefix(prefixes)
    process(conn, fixture_messages(reference))
    expected = table_digest(conn, reference)

    prefix = new_prefix(prefixes)
    messages = fixture_messages(prefix)
    producer = Producer({"bootstrap.servers": BOOTSTRAP, "enable.idempotence": True})
    for m in messages:
        producer.produce(topic, m.value(), m.key(), headers=m.headers())
    assert producer.flush(15) == 0

    group = f"it-{uuid.uuid4().hex[:8]}"
    first = Harness(consumer(group), crash_on_commit=2)
    first.inner.subscribe([topic])
    with pytest.raises(Crash):
        run(first, conn, first, max_messages=200, max_wait_s=1.0)
    first.inner.close()  # no commit: auto commit is off
    assert first.seen < len(messages)  # crashed part-way

    probe = consumer(group)
    committed = probe.committed([TopicPartition(topic, p) for p in range(6)], timeout=10)
    probe.close()
    assert 0 < sum(max(tp.offset, 0) for tp in committed) < len(messages)

    second = Harness(consumer(group))
    second.inner.subscribe([topic])
    run(second, conn, second, max_messages=200, max_wait_s=1.0)
    second.inner.close()
    # The batch whose offsets weren't committed was consumed twice.
    assert first.seen + second.seen > len(messages)

    assert table_digest(conn, prefix) == expected
