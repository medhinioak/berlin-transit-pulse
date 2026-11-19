# stop-event-processor

Consumes the Kafka topic `trip-updates` (contract:
[schemas/trip-updates.md](../../schemas/trip-updates.md)) and keeps the latest known state of
every trip and every stop in Postgres. Replaying the topic, duplicate deliveries and late
messages always give the same tables. Turning predictions into observed stop events is M3
(see below).

Data: VBB Verkehrsverbund Berlin-Brandenburg GmbH, CC-BY 4.0
(<https://creativecommons.org/licenses/by/4.0/>).

## Run

```sh
make up                     # compose runs it as the stop-event-processor container
BTP_KAFKA_GROUP=dev-$USER uv run btp-processor   # on the host, against the compose stack
```

On the host, use your own group: a second member in `btp-processor` splits the partitions
with the compose container and makes both rebalance.

It applies `src/btp_processor/sql/001_schema.sql` on start (idempotent) as `btp_processor`,
then consumes in group `btp-processor`. A new group starts at the oldest retained message
(24 h), which is safe because writes are idempotent. On SIGTERM or SIGINT it finishes and
commits the current batch, closes the consumer and exits with 0. It exits with 1 on a
database error, a fatal Kafka error or a missing topic (auto-creation is off), so the
container restarts and consumes the uncommitted batch again; with 2 if the settings are
invalid. A commit that fails without a fatal error (e.g. partitions revoked in a rebalance)
is logged as a warning (`committed: false`) and the batch is consumed again later.

| Variable | Default | Meaning |
| --- | --- | --- |
| `BTP_KAFKA_BOOTSTRAP` | `localhost:9094` | broker; compose sets `kafka:9092` |
| `BTP_KAFKA_TOPIC` | `trip-updates` | topic to consume |
| `BTP_KAFKA_GROUP` | `btp-processor` | consumer group |
| `BTP_PG_HOST`, `BTP_PG_PORT`, `BTP_PG_DB` | `localhost`, `5432`, `btp` | Postgres |
| `BTP_PG_USER` | `btp_processor` | role; owns schema `events` |
| `BTP_PG_PROCESSOR_PASSWORD` | `dev_processor` | the compose development password |
| `BTP_BATCH_MAX_MESSAGES` | `500` | a batch ends after this many messages… |
| `BTP_BATCH_MAX_WAIT_S` | `1` | …or after this many seconds |
| `BTP_LOG_LEVEL` | `INFO` | log level |

## Tables

Schema `events`, owned by `btp_processor`; `btp_metrics_jobs` can read it. All timestamps
are `timestamptz` (UTC). `updated_at` is the wall-clock time of the last write and is the
only column that differs between two replays.

`events.trip_state`: one row per trip and service day, primary key
(`trip_id`, `service_date`).

| Column | From |
| --- | --- |
| `trip_id`, `service_date` | `TripDescriptor.trip_id`, `start_date` (`YYYYMMDD`) |
| `route_id`, `start_time` | as sent, `NULL` if empty (VBB sends no `start_time`). `start_time` is text: GTFS times can exceed 24:00 |
| `schedule_relationship` | `SCHEDULED`, `CANCELED`, `ADDED`, … |
| `feed_ts` | `btp-feed-ts` header of the last message applied to the row |
| `removed_at` | `feed_ts` of the tombstone, `NULL` while the trip is in the feed |

`events.stop_predictions`: one row per trip, service day and stop, primary key
(`trip_id`, `service_date`, `stop_sequence`), index on (`service_date`, `departure_time`).

| Column | From |
| --- | --- |
| `stop_sequence`, `stop_id` | the `StopTimeUpdate` |
| `arrival_time`, `departure_time` | `StopTimeEvent.time`, `NULL` if absent or 0 |
| `arrival_delay`, `departure_delay` | `StopTimeEvent.delay` in seconds, `NULL` if absent (not 0) |
| `stop_schedule_relationship` | `SCHEDULED`, `SKIPPED`, `NO_DATA`, … |
| `feed_ts` | `btp-feed-ts` of the message the row was last written from |

A stop that disappears from a later message of its trip keeps its last row: the table holds
the latest prediction each stop ever had, not only the stops of the latest message.

## Delivery and ordering

- **At least once.** Auto commit is off. A batch (up to 500 messages or 1 s) is written in
  one transaction; its offsets are committed after that transaction commits. A crash in
  between consumes the batch again. Trip writes run in delivery order (consecutive messages
  of one kind pipelined with `executemany`); the batch's stops go in one statement.
- **Ordering guard.** A message changes a row only if its `btp-feed-ts` is `>=` the row's
  `feed_ts`. So every row ends as the newest message that touched it, whatever the order,
  batching or number of deliveries; on equal `feed_ts` the message delivered last wins.
  Older messages are ignored and counted.
- **Tombstones** set `removed_at` and `feed_ts` on `trip_state`, keep the trip's last state,
  and don't touch `stop_predictions`. A later update with a newer `feed_ts` (the trip is back
  in the feed) clears `removed_at`; an older one can't. A tombstone for a trip that was
  never stored changes nothing.
- **new and changed** are treated the same: the value is the trip's full state.

## What is skipped (M1)

| Case | What happens | Counter in the batch log |
| --- | --- | --- |
| No `start_date` (key `trip_id\|`) | message skipped | `skipped_no_start_date` |
| A stop with only `stop_id`, no `stop_sequence` | that stop skipped, rest of the trip stored | `skipped_no_stop_sequence` |
| Undecodable value, bad key or missing `btp-feed-ts` | message skipped, offset committed, logged as a warning with the first offending offset | `skipped_invalid` |

How to resolve the first two is for M3 to decide from these counts. The recorded fixtures
have stop_id-only stops (~2 % of stops) and no trip without `start_date`.

## Batch log

One JSON line per batch: `messages`, `trips_upserted`, `trips_ignored_older`,
`removals_applied`, `removals_ignored`, `stops_upserted`, `stops_ignored_older`, the three
`skipped_*` counters, `committed` (plus `commit_error` if false), `lag` (messages behind the end of the assigned partitions, from cached
watermarks; `null` if unknown) and `batch_ms`.

## Fixtures mode

Ingest replays the four recorded snapshots in a loop and rewrites only the header time
(#8 comment). After each loop trips that had finished or vanished come back with a newer
`feed_ts`: their `removed_at` is cleared and their stops revert to the recorded state. The
tables are right for the messages received; they just don't describe a real day.

## Not done yet

- Finalising observed stop events with a grace window, and matching to the schedule (M3;
  the definition is planned ADR-0004).
- Resolving stop_id-only updates and missing `start_date`.
- Retention or partitioning of the events tables (M3).

## Tests

```sh
uv run pytest services/stop-event-processor                   # unit: parse.py, pure store logic
make up && uv run pytest -m integration services/stop-event-processor
```

The integration tests write trips with their own `trip_id` prefix and delete them
afterwards, so they run next to the compose processor. They cover replay idempotency (also
with one-message batches), the ordering guard, tombstones, and a crash after the database
commit but before the offset commit (on a temporary topic).
