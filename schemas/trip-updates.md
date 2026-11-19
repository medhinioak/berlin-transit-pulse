# Topic `trip-updates`

The ingest service compares each VBB GTFS-RT snapshot (every 30 s) with the previous one and
publishes only what changed: one message per new or changed trip, and a tombstone per trip
that disappeared. Producer: ingest (#7). Consumer: processor (#8).

| Setting | Value |
| --- | --- |
| Partitions | 6 (created by `kafka-init`, deploy/compose/compose.yaml) |
| Replication | 1 (single broker) |
| Retention | 24 h (`retention.ms=86400000`) |
| Cleanup policy | `delete` (not compacted; tombstones are markers for consumers, not deletions) |

## Key

`"{trip_id}|{start_date}"` (UTF-8), from the TripUpdate's trip descriptor, e.g.
`279146123|20261118`. If `start_date` is empty the separator stays: `279146123|`.
None of the recorded snapshots (fixtures, and ~11,100 live trips on 2026-11-19) has an
empty `start_date`. If VBB ever sent the same trip with and without it, the two would be
separate keys, and the switch would show up as a `new` plus a `removed`.

The key never uses `FeedEntity.id`: VBB changes the entity ID whenever a trip's content
changes, so it doesn't identify a trip across snapshots.

## Value

| Event | Value |
| --- | --- |
| `new`, `changed` | The GTFS-RT `TripUpdate` protobuf (`transit_realtime.TripUpdate`, GTFS-RT 2.0) as in the feed, serialized as bytes. Nothing is changed, the trip-level `timestamp` included |
| `removed` | `null` (tombstone) |

Decode with `gtfs-realtime-bindings`: `TripUpdate.FromString(value)`.

## Headers

All values are UTF-8 bytes.

| Header | Content |
| --- | --- |
| `btp-feed-ts` | Feed header timestamp of the snapshot, epoch seconds, e.g. `1795021421` |
| `btp-snapshot` | The snapshot's HTTP ETag, or the feed timestamp if there was none. Same for every message of one snapshot |
| `btp-event` | `new`, `changed` or `removed` |
| `btp-schema` | `gtfs-rt.TripUpdate/2.0` |

## Events

- **new**: the key wasn't in the last published snapshot (or ingest restarted, see below).
- **changed**: the key was there with different content. Content is compared with the
  `timestamp` field cleared, so a trip whose only change is its timestamp isn't published.
- **removed**: the key was in the last published snapshot and isn't in this one. The feed
  drops trips some time after they finish, and sometimes before; a removal is not a
  cancellation (cancellations arrive as `changed` with `schedule_relationship: CANCELED`).

If one snapshot has two entities with the same key, the last one is published and ingest
logs the duplicate count.

## Guarantees

- **Ordering per key.** One key always goes to the same partition (librdkafka's default
  partitioner, a hash of the key), and the producer is idempotent (`enable.idempotence=true`,
  `acks=all`), so the events of one trip arrive in the order they were published. There is
  no ordering across keys or partitions.
- **At least once.** Ingest updates its state only after every message of a snapshot was
  delivered. A message that isn't delivered within 10 s (`BTP_PUBLISH_TIMEOUT_S`) fails.
  If any message fails, the next snapshot is diffed against the old state again, so some
  messages are published twice.
- **Restarts.** Ingest keeps its state in memory. After a restart the first snapshot
  publishes every trip as `new` (~10,000 messages), including trips that are unchanged.
  Removals for trips that vanished while ingest was down are never published.
- **Order of snapshots.** A snapshot whose header timestamp is older than the last
  published one is skipped.

## What consumers must do

- Be idempotent: the same TripUpdate can arrive more than once, as `new` or `changed`.
  Treat `new` and `changed` the same way: the value is the trip's full current state.
- Use the TripUpdate content (and `btp-feed-ts`), not the event type, to decide what
  happened to a trip.
- Treat a tombstone as "no longer in the feed": finalise what's known about the trip. Don't
  expect a tombstone for every trip, see Restarts.

## Volume

Measured on the committed fixtures (trimmed to ~166 trips, so the shares are higher than
on the full feed) and on the live feed: see the PR for #7 and ADR-0003.
