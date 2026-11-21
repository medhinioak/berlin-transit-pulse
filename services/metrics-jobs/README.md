# metrics-jobs

Owns everything in the Postgres schema `marts`. For now that is two plain SQL views, applied
by `btp-metrics-jobs apply-sql` as `btp_metrics_jobs`. dbt models replace the plain SQL in M4.
metrics-api reads `marts` through the read-only role `btp_api`; nothing else writes here.

Data: VBB Verkehrsverbund Berlin-Brandenburg GmbH, CC-BY 4.0
(<https://creativecommons.org/licenses/by/4.0/>).

## Run

```sh
make up                                  # compose runs it once as metrics-jobs-apply
uv run btp-metrics-jobs apply-sql        # on the host, against the compose stack
```

`apply-sql` applies every `sql/views/*.sql` in name order, in one transaction: all of them or
none. Each file uses `CREATE OR REPLACE VIEW`, so running it again changes nothing. Postgres
can't drop, rename or retype a view's columns with `CREATE OR REPLACE`: such a change
needs a `DROP VIEW` in the same file (and the consumers checked).

The views read `events.*`, which the processor creates when it starts. Compose starts both
together, so `apply-sql` retries for up to `BTP_WAIT_FOR_TABLES_S` (60 s) while a source table
doesn't exist. Exit codes: 0 done, 1 database error, 2 invalid settings or no SQL files.

| Variable | Default | Meaning |
| --- | --- | --- |
| `BTP_PG_HOST`, `BTP_PG_PORT`, `BTP_PG_DB` | `localhost`, `5432`, `btp` | Postgres |
| `BTP_PG_USER` | `btp_metrics_jobs` | role; owns schema `marts`, reads `events` and `schedule` |
| `BTP_PG_METRICS_JOBS_PASSWORD` | `dev_metrics_jobs` | the compose development password |
| `BTP_METRICS_SQL_DIR` | `sql/` in this folder (`/app/sql` in the image) | where `views/` is |
| `BTP_WAIT_FOR_TABLES_S` | `60` | how long to wait for the source tables |

## Views

### `marts.v_now_by_mode`: a live indicator, not a reliability metric

Trips running in Berlin right now and their current delay, one row per mode. It is **not**
punctuality, an on-time rate or any other reliability metric: those need the metric
definitions of ADR-0005 (M3). Don't present it as one.

| Column | Meaning |
| --- | --- |
| `mode` | from the active schedule (`schedule.v_routes.mode`, ADR-0017) |
| `running_trips` | Berlin trips running now (definition below) |
| `trips_with_nonzero_delay` | running trips whose current delay is known and not 0 |
| `avg_delay_s`, `p50_delay_s`, `p95_delay_s` | mean, median and 95th percentile (`percentile_cont`) of the current delay, seconds, rounded to 0.1 s; `NULL` if no running trip has a delay |
| `cancelled_trips_today` | `CANCELED` Berlin trips of today's service day (Europe/Berlin), also those already removed from the feed |
| `latest_feed_ts` | newest `feed_ts` in `events.trip_state` |
| `computed_at` | `now()` when the row was computed |

- **Now** is the session setting `btp.as_of` if set, else `now()`
  ([ADR-0019](../../docs/adr/0019-pin-the-reference-time-of-live-views-with-a-session-setting.md)).
  metrics-api sets it in fixtures mode to the recording time; in live mode it is `now()`.
- **Stops**: only stops with `stop_schedule_relationship = 'SCHEDULED'` count, below. SKIPPED
  and NO_DATA stops have predicted times but never a delay (live data, 2026-11-19: 2,918 and
  228 of 184k stop rows). Counting them would give a NULL current delay when the next stop is
  skipped, and could stretch a trip's running window.
- **Running trip**: in `events.trip_state`, not removed, not `CANCELED`, in Berlin
  (`schedule.v_trips.in_berlin`), and the earliest predicted time of its stops <= now <=
  the latest one. Predicted time is `departure_time`, else `arrival_time`. Trips that haven't
  started or have finished are left out, so their zero delays don't count (CLAUDE.md, Gotchas).
- **Current delay**: at the first stop (by `stop_sequence`) whose predicted time is at or
  after now: `departure_delay`, else `arrival_delay`. A running trip with neither there
  counts in `running_trips` but not in the delay columns.
- Only the service days today and yesterday are read: a trip running now started on one of
  them (yesterday covers trips past midnight).
- Trips missing from the active schedule (e.g. `ADDED` trips) have no mode and are left out.
- A mode appears when it has running trips or cancellations today. No running trips (late
  at night, or a feed outage) gives no rows, not zeros.

### `marts.v_feed_status`

One row: `latest_feed_ts` (newest `feed_ts` the processor applied) and `computed_at`.
metrics-api uses it for `/health`, since `btp_api` can't read `events`.

## Performance

The view starts from the trips still in the feed (`removed_at IS NULL`; ingest tombstones
trips that leave it, so ~10k) and reads each one's stops through the `stop_predictions`
primary key. It never aggregates every prediction of the day. Measured on 2026-11-19:

| Data | Rows in `stop_predictions` | Open trips | Time |
| --- | ---: | ---: | ---: |
| fixtures (rush hour), next to the live rows below | ~222k | ~170 | 53 ms |
| synthetic full Wednesday, 2 service days, rows clustered by trip | 2.0 M | 12k | ~350 ms warm, ~700 ms cold |
| same, rows scattered over the table | 2.0 M | 12k | ~3.5 s |
| live feed, 10 min (20 polls) | 218k | 10.3k | 124 ms warm, 716 ms right after a Postgres restart |

Time grows with the number of open trips, not the size of the table. The first query after
a Postgres restart pays for an empty cache; the 500 ms target (#9) holds once warm. Without retention
(M3), `trip_state` and `latest_feed_ts`'s `max(feed_ts)` scan grow by ~80k rows a day.

## Known limitations

- **Timetable change night** (2026-12-13): the view finds a trip's mode and Berlin flag only in
  the active schedule version. Trips of the previous service day still running after
  midnight keep their old trip_ids, so after the new version is activated they drop out
  until they finish. Handling version swaps is for M3, with the weekly refresh (#24).
- **Mixed modes**: compose runs every service in fixtures mode by default, and the mini GTFS
  matches the fixture trips. Importing the live static GTFS while replaying fixtures works only
  while the live GTFS still contains the 2026-11-18 trip_ids; after the timetable change on
  2026-12-13 the fixtures-mode endpoint comes back empty. Set `BTP_FEED_MODE` for the whole
  stack, not per service.
- **No retention yet** (M3): `max(feed_ts)` in `v_feed_status` and the `service_date` filter
  on `trip_state` are sequential scans. They take ~20 ms at ~80k rows (one day) and grow with
  every day kept. Retention or an index on `trip_state` (processor schema) comes before it
  matters.

## Tests

```sh
uv run pytest services/metrics-jobs                                # unit: file order, waiting
make up && uv run pytest -m integration services/metrics-jobs
```

The integration tests write hand-made trips as `btp_processor` on service days in 2030, pin
`btp.as_of` there and read the view as `btp_api`. They cover trips past midnight, not started,
finished, removed, cancelled today and yesterday, outside Berlin, skipped stops, the
arrival-delay fallback,
a trip with no delay, a mode with only cancellations, and re-applying the SQL.
