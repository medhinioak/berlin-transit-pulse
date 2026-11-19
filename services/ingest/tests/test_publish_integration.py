"""Integration test: publish a fixture set to the compose Kafka and read it back.

Run with `make up` and then `make test-integration`. Uses its own temporary topic, so the
compose ingest container publishing to trip-updates doesn't interfere.
"""

import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from confluent_kafka import Consumer, Message
from confluent_kafka.admin import AdminClient, NewTopic

from btp_common.fixtures import load_rt_fixture
from btp_ingest.diff import diff
from btp_ingest.feed import Snapshot
from btp_ingest.publish import Publisher, make_producer

pytestmark = pytest.mark.integration

BOOTSTRAP = os.environ.get("BTP_KAFKA_BOOTSTRAP", "localhost:9094")
START = datetime(2026, 11, 18, 17, 3, 41, tzinfo=UTC)


@pytest.fixture
def topic() -> Iterator[str]:
    """Create a 6-partition test topic and delete it afterwards."""
    admin = AdminClient({"bootstrap.servers": BOOTSTRAP})
    name = f"it-trip-updates-{uuid.uuid4().hex[:8]}"
    admin.create_topics([NewTopic(name, num_partitions=6, replication_factor=1)])[name].result(
        timeout=15
    )
    yield name
    admin.delete_topics([name])[name].result(timeout=15)


def _consume_all(topic: str, expected: int) -> list[Message]:
    """Read the topic from the start until expected messages arrived (or 30 s passed)."""
    consumer = Consumer(
        {
            "bootstrap.servers": BOOTSTRAP,
            "group.id": f"it-{uuid.uuid4().hex[:8]}",
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,
        }
    )
    consumer.subscribe([topic])
    messages: list[Message] = []
    deadline = datetime.now(UTC) + timedelta(seconds=30)
    try:
        while len(messages) < expected and datetime.now(UTC) < deadline:
            msg = consumer.poll(1.0)
            if msg is None:
                continue
            assert msg.error() is None, msg.error()
            messages.append(msg)
    finally:
        consumer.close()
    return messages


def test_fixture_set_round_trip(topic: str) -> None:
    """Messages, keys, headers and tombstones on the topic match the diffs of the set."""
    snapshots = load_rt_fixture("rush-hour")
    publisher = Publisher(make_producer(BOOTSTRAP, 10.0), topic, 10.0)
    expected: list[tuple[str, bytes]] = []
    state: dict[str, bytes] = {}
    for i, message in enumerate(snapshots):
        changes = diff(state, message)
        expected += [(k, b"new") for k, _ in changes.new]
        expected += [(k, b"changed") for k, _ in changes.changed]
        expected += [(k, b"removed") for k in changes.removed]
        state = changes.fingerprints
        at = START + timedelta(seconds=30 * i)
        snapshot = Snapshot(at, at, None, 0, 0, message)
        assert publisher.handle(snapshot)

    messages = _consume_all(topic, len(expected))
    got = []
    for msg in messages:
        headers = dict(msg.headers() or [])
        key = msg.key()
        assert isinstance(key, bytes)
        event = headers["btp-event"]
        assert isinstance(event, bytes)
        assert headers["btp-schema"] == b"gtfs-rt.TripUpdate/2.0"
        assert (msg.value() is None) == (event == b"removed")
        got.append((key.decode(), event))
    assert sorted(got) == sorted(expected)

    # Per key, events arrive in publish order (one key always lands in one partition).
    for trip in {k for k, _ in expected}:
        assert [e for k, e in got if k == trip] == [e for k, e in expected if k == trip]
