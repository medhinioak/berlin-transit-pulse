"""Compare a feed snapshot with the previous one, trip by trip (pure functions, no Kafka)."""

import hashlib
from dataclasses import dataclass, field

from google.transit.gtfs_realtime_pb2 import FeedMessage, TripUpdate

# Last published fingerprint per trip key.
Fingerprints = dict[str, bytes]


def trip_key(tu: TripUpdate) -> str:
    """Return the key "{trip_id}|{start_date}"; the separator stays if start_date is empty.

    Never the FeedEntity.id: VBB changes it whenever a trip's content changes.
    """
    return f"{tu.trip.trip_id}|{tu.trip.start_date}"


def fingerprint(tu: TripUpdate) -> bytes:
    """Hash the trip update serialized deterministically, ignoring its timestamp field."""
    copy = TripUpdate()
    copy.CopyFrom(tu)
    copy.ClearField("timestamp")
    return hashlib.blake2b(copy.SerializeToString(deterministic=True), digest_size=16).digest()


@dataclass
class Changes:
    """What changed between two snapshots, and the fingerprints to keep if it's published."""

    new: list[tuple[str, TripUpdate]] = field(default_factory=list)
    changed: list[tuple[str, TripUpdate]] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    duplicates: int = 0  # entities dropped because a later one had the same key
    total: int = 0  # distinct trips in the snapshot
    fingerprints: Fingerprints = field(default_factory=dict)

    @property
    def events(self) -> int:
        """Return the number of messages to publish."""
        return len(self.new) + len(self.changed) + len(self.removed)

    @property
    def change_rate_pct(self) -> float:
        """Return messages to publish as a share of the snapshot's trips, in percent."""
        return round(100 * self.events / self.total, 2) if self.total else 0.0


def diff(previous: Fingerprints, message: FeedMessage) -> Changes:
    """Return new, changed and removed trips of message compared with previous.

    Entities without a trip update are ignored. If two entities share a key, the last one wins
    and the other is counted in duplicates.
    """
    latest: dict[str, TripUpdate] = {}
    duplicates = 0
    for entity in message.entity:
        if not entity.HasField("trip_update"):
            continue
        key = trip_key(entity.trip_update)
        if key in latest:
            duplicates += 1
        latest[key] = entity.trip_update

    changes = Changes(duplicates=duplicates, total=len(latest))
    for key, tu in latest.items():
        fp = fingerprint(tu)
        changes.fingerprints[key] = fp
        old = previous.get(key)
        if old is None:
            changes.new.append((key, tu))
        elif old != fp:
            changes.changed.append((key, tu))
    changes.removed = [key for key in previous if key not in latest]
    return changes
