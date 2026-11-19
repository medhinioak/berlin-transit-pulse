"""Unit tests for the pure parts of store.py and consumer.py (no Postgres, no Kafka)."""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

from confluent_kafka import TopicPartition

from btp_processor.consumer import lag, offsets_to_commit
from btp_processor.parse import StopRow
from btp_processor.store import STOP_COLUMNS, latest_stops, stop_columns

T0 = datetime(2026, 11, 18, 17, 0, tzinfo=UTC)
ROW = StopRow(
    trip_id="1",
    service_date=date(2026, 11, 18),
    stop_sequence=3,
    stop_id="de:11000:900100001::1",
    arrival_time=T0,
    arrival_delay=60,
    departure_time=T0,
    departure_delay=60,
    stop_schedule_relationship="SCHEDULED",
    feed_ts=T0,
)


def test_latest_stops_keeps_newest_and_counts_older() -> None:
    """Per stop key the newest feed_ts wins; an older row after it is counted."""
    newer = replace(ROW, arrival_delay=120, feed_ts=T0 + timedelta(seconds=30))
    other_stop = replace(ROW, stop_sequence=4)
    rows, older = latest_stops([ROW, newer, replace(ROW, arrival_delay=0), other_stop])
    assert older == 1
    assert sorted(rows, key=lambda r: r.stop_sequence) == [newer, other_stop]


def test_latest_stops_tie_goes_to_the_later_row() -> None:
    """Equal feed_ts: the later row wins (as applying them in order would), nothing counted."""
    later = replace(ROW, arrival_delay=90)
    rows, older = latest_stops([ROW, later])
    assert rows == [later]
    assert older == 0


def test_latest_stops_separates_service_dates() -> None:
    """The same trip_id on two service dates is two stops."""
    next_day = replace(ROW, service_date=date(2026, 11, 19))
    rows, older = latest_stops([ROW, next_day])
    assert len(rows) == 2 and older == 0


def test_stop_columns_transposes_in_column_order() -> None:
    """One list per column, in STOP_COLUMNS order, one value per row."""
    cols = stop_columns([ROW, replace(ROW, stop_sequence=4, stop_id=None)])
    assert len(cols) == len(STOP_COLUMNS)
    assert cols[STOP_COLUMNS.index("stop_sequence")] == [3, 4]
    assert cols[STOP_COLUMNS.index("stop_id")] == ["de:11000:900100001::1", None]


class FakeMessage:
    """A message on trip-updates with only a partition and an offset."""

    def __init__(self, partition: int, offset: int) -> None:
        """Remember the partition and offset."""
        self._partition, self._offset = partition, offset

    def key(self) -> bytes | None:
        """Return no key."""
        return None

    def value(self) -> bytes | None:
        """Return no value."""
        return None

    def headers(self) -> None:
        """Return no headers."""
        return None

    def error(self) -> None:
        """Return no error."""
        return None

    def topic(self) -> str:
        """Return the topic."""
        return "trip-updates"

    def partition(self) -> int:
        """Return the partition."""
        return self._partition

    def offset(self) -> int:
        """Return the offset."""
        return self._offset


def test_offsets_to_commit_is_last_plus_one_per_partition() -> None:
    """The commit points after the highest offset of each partition in the batch."""
    batch = [FakeMessage(0, 5), FakeMessage(1, 9), FakeMessage(0, 7), FakeMessage(0, 6)]
    got = offsets_to_commit(batch)
    assert [(tp.topic, tp.partition, tp.offset) for tp in got] == [
        ("trip-updates", 0, 8),
        ("trip-updates", 1, 10),
    ]
    assert all(isinstance(tp, TopicPartition) for tp in got)


class NoAssignment:
    """A consumer with no partitions assigned, as during a rebalance."""

    def assignment(self) -> list[TopicPartition]:
        """Return no partitions."""
        return []


def test_lag_is_unknown_without_assignment() -> None:
    """No assigned partitions means lag is unknown, not 0."""
    assert lag(NoAssignment()) is None  # type: ignore[arg-type]
