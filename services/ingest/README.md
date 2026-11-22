# ingest

Polls VBB's GTFS-Realtime feed, validates each snapshot, compares it with the last published
one and publishes only new, changed and removed trips to the Kafka topic `trip-updates`
(contract: [schemas/trip-updates.md](../../schemas/trip-updates.md)). A fixtures mode replays
recorded snapshots, so the stack runs without network.

Data: VBB Verkehrsverbund Berlin-Brandenburg GmbH, CC-BY 4.0
(<https://creativecommons.org/licenses/by/4.0/>), from <https://production.gtfsrt.vbb.de/data>.

## Run

```sh
BTP_FEED_MODE=fixtures uv run btp-ingest                         # offline replay
BTP_FEED_MODE=live BTP_FEED_CONTACT=<email or URL> uv run btp-ingest   # the real feed
```

The service stops on SIGTERM or SIGINT. It finishes the current poll, flushes Kafka, closes the
connection and exits with 0. In live mode it waits one poll interval before the first request,
so a crash-restart loop can't exceed VBB's rate limit (the rate guard lives in one process).
It exits with 2 if the settings are invalid, if live mode has no contact, or if the fixture
set doesn't exist, and with 1 after a fatal Kafka producer error (the idempotent producer
can't be used again, so the container restarts with a new one).

| Variable | Default | Meaning |
| --- | --- | --- |
| `BTP_FEED_MODE` | `live` | `live` polls VBB; `fixtures` replays a recorded set (compose uses this) |
| `BTP_FEED_CONTACT` | none | email or URL for the User-Agent; **required in live mode** |
| `BTP_FEED_URL` | `https://production.gtfsrt.vbb.de/data` | the realtime feed |
| `BTP_POLL_INTERVAL_S` | `30` | seconds from one poll's start to the next; below 10 is refused |
| `BTP_FEED_MAX_AGE_S` | `180` | an older header is logged as stale but still passed on |
| `BTP_FIXTURE_SET` | `rush-hour` | `fixtures/vbb-rt/<set>/` to replay (`rush-hour`, `evening`) |
| `BTP_FIXTURES_DIR` | `fixtures/` in the checkout | set it in containers (the package isn't in the repo there) |
| `BTP_KAFKA_BOOTSTRAP` | `127.0.0.1:9094` | broker; compose sets `kafka:9092` |
| `BTP_KAFKA_TOPIC` | `trip-updates` | topic to publish to |
| `BTP_PUBLISH_TIMEOUT_S` | `10` | a message not delivered within this fails (the producer's `message.timeout.ms`); the flush waits 5 s longer, so nothing carries over to the next snapshot |
| `BTP_LOG_LEVEL` | `INFO` | log level |

## Politeness (live mode)

VBB allows 7 requests per minute and blocks generic clients. Each rule is enforced in
`feed.py` and has a test in `tests/test_feed.py` or `tests/test_runner.py`:

| Rule | How |
| --- | --- |
| At most 6 requests in any 60 s, retries included | `RateGuard`, a sliding window checked before every request, separate from the interval and the backoff |
| gzip | `Accept-Encoding: gzip`; the gzipped size is logged as `wire_bytes` |
| Conditional requests | `If-None-Match` with the last ETag; a 304 means no new data. The ETag only moves after a valid snapshot |
| Contact | `User-Agent: berlin-transit-pulse/<version> (+<BTP_FEED_CONTACT>)`; no contact, no start |
| Backoff | After 429, 5xx, any other error status and network errors: 30 s, doubling up to 5 min. `Retry-After` (seconds or a date) wins when longer, even above 5 min, up to 1 h. A success or 304 resets it |

Invalid snapshots are counted (`invalid_total`) and skipped, and polling continues at the
normal interval. A snapshot is invalid if it isn't a FeedMessage, isn't valid gzip, isn't
GTFS-RT 1.0/2.0, isn't FULL_DATASET, or has no header timestamp.

Tests never call the feed: they use `httpx.MockTransport`, the recorded fixtures and a fake
clock.

## Fixtures mode

Replays `fixtures/vbb-rt/<BTP_FIXTURE_SET>/*.pb.gz` in order at the poll interval and loops.
Each snapshot's header timestamp is set to now, so freshness checks behave as in live mode.
Trip-level times (arrival and departure, trip update timestamps) stay as recorded on
2026-11-18. ETags come from the set's `manifest.json`.

## Log line

Every poll writes one JSON line with the message `poll`. For example, from fixtures mode:

```json
{"ts": "2026-11-18T23:30:32.048889+00:00", "level": "INFO", "service": "ingest",
 "logger": "btp_ingest.runner", "message": "poll", "status": "200", "wire_bytes": 46743,
 "raw_bytes": 203427, "entities": 166, "header_age_s": 0.0,
 "etag": "\"2416f7-BkYzIz6ekT3qmKk3S7Bmx64FWHQ\"", "duration_ms": 83, "invalid_total": 0}
```

`status` is always a string: the HTTP code (`"200"`, `"304"`, `"429"`, `"503"`), `"network"`
or `"invalid"`. Failures are logged at WARNING level and add `next_poll_s`, `retry_after_s` and,
where there is one, a `reason`.

## Publishing changes

`diff.py` keys each trip by `"{trip_id}|{start_date}"` (never `FeedEntity.id`, which VBB
changes with the content) and fingerprints it: a hash of the TripUpdate serialized
deterministically with `timestamp` cleared. Equal fingerprint, nothing published.

`publish.py` sends new and changed trips (the TripUpdate bytes) and a tombstone per vanished
trip, flushes, and only then keeps the snapshot's fingerprints. If any message fails, the
state stays as it was and the next snapshot re-sends the changes; a failure is logged, not
raised, so polling continues. Fingerprints live in memory: after a restart every trip is sent
as `new`. A snapshot whose header timestamp is older than the last published one is skipped;
an equal timestamp is diffed as usual (a repeat publishes nothing).

After each snapshot one `publish` line follows the `poll` line:

```json
{"ts": "2026-11-19T05:03:58.955038+00:00", "level": "INFO", "service": "ingest",
 "logger": "btp_ingest.publish", "message": "publish", "trips": 168, "new": 5, "changed": 16,
 "removed": 3, "duplicates": 0, "change_rate_pct": 14.29, "publish_ms": 45, "committed": true}
```

`change_rate_pct` is (new + changed + removed) / trips. When `committed` is false the line is a
WARNING and adds `failed` and `still_queued`. Duplicate keys in one snapshot get one extra
WARNING line.
