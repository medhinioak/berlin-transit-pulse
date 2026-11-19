# metrics-api

Read-only HTTP API over the `marts` schema. FastAPI and uvicorn on port 8000, connected as the
role `btp_api`, which can read `marts` and nothing else. It never computes figures itself:
every number comes from a view owned by metrics-jobs ([README](../metrics-jobs/README.md)).

Data: VBB Verkehrsverbund Berlin-Brandenburg GmbH, CC-BY 4.0
(<https://creativecommons.org/licenses/by/4.0/>). Every data response carries this
attribution.

## Run

```sh
make up                                          # compose: http://localhost:8000
BTP_FEED_MODE=fixtures uv run btp-metrics-api    # on the host (127.0.0.1:8000)
```

OpenAPI docs: <http://localhost:8000/docs>. Logs are JSON lines via `btp_common`, uvicorn's
access log included. The connection pool opens without waiting, so the API starts while the
database is down; `/health` then answers 503 and the data endpoint 503.

| Variable | Default | Meaning |
| --- | --- | --- |
| `BTP_HOST`, `BTP_PORT` | `127.0.0.1`, `8000` | listen address; compose sets `0.0.0.0` |
| `BTP_PG_HOST`, `BTP_PG_PORT`, `BTP_PG_DB` | `localhost`, `5432`, `btp` | Postgres |
| `BTP_PG_USER` | `btp_api` | read-only role |
| `BTP_PG_API_PASSWORD` | `dev_api` | the compose development password |
| `BTP_POOL_MAX_SIZE` | `4` | connections in the pool |
| `BTP_FEED_MAX_AGE_S` | `300` | `/health` fails when the newest feed time is this old |
| `BTP_FEED_MODE` | `live` (compose: `fixtures`, like ingest) | `fixtures` pins "now" to the recording time |
| `BTP_FIXTURE_SET` | `rush-hour` | the set ingest replays; its last snapshot's time becomes "now" |
| `BTP_AS_OF` | unset | pins "now" explicitly (ISO 8601 with an offset, e.g. `2026-11-18T17:04:51Z`; a time without one is refused); overrides the fixture time |
| `BTP_FIXTURES_DIR` | `fixtures/` in the checkout (`/fixtures` in compose) | where the fixture sets are |

### Reference time

The "now" views use the session setting `btp.as_of`, or `now()` when it isn't set
([ADR-0018](../../docs/adr/0018-pin-the-reference-time-of-live-views-with-a-session-setting.md)).
In fixtures mode the stop times are those recorded on 2026-11-18, so the API pins `btp.as_of`
to the header time of the replayed set's last snapshot (rush-hour: 2026-11-18T17:04:51Z). In
live mode it sets nothing. The value is logged at start (`started`, field `as_of`) and
returned as `as_of`.

## Endpoints

### `GET /health`

200 when the database answers and `marts.v_feed_status.latest_feed_ts` is less than 5 minutes
old, else 503 with the reason (`database unavailable: …`, `no feed data in the database`,
`feed data is N s old (limit 300 s)`, `feed data is N s in the future` when it is more than 60 s ahead of the API clock).

```json
{"status": "ok", "reason": null, "latest_feed_ts": "2026-11-19T10:08:19Z", "feed_age_s": 3.5}
```

The compose healthcheck only checks that the server answers (200 or 503): a stale feed is
for monitoring to report, not a reason for `make up` to fail.

### `GET /v1/now/by-mode`

A **live indicator**: trips running in Berlin now and their current delay per mode, from
`marts.v_now_by_mode`. It is not punctuality or an on-time rate (those wait for ADR-0005,
M3). Definitions of every field: [metrics-jobs README](../metrics-jobs/README.md#martsv_now_by_mode-a-live-indicator-not-a-reliability-metric).
Delays are in seconds. `Cache-Control: public, max-age=30`.

```json
{
  "kind": "live-indicator",
  "as_of": "2026-11-18T17:04:51Z",
  "attribution": "Data: VBB Verkehrsverbund Berlin-Brandenburg GmbH, CC-BY 4.0",
  "modes": [
    {
      "mode": "tram",
      "running_trips": 19,
      "trips_with_nonzero_delay": 15,
      "avg_delay_s": 375.8,
      "p50_delay_s": 120.0,
      "p95_delay_s": 1620.0,
      "cancelled_trips_today": 0,
      "latest_feed_ts": "2026-11-19T10:09:05Z",
      "computed_at": "2026-11-19T10:09:13.686002Z"
    }
  ]
}
```

(fixtures mode, one mode shown). `as_of` is the pinned reference time, or in live mode the
database time the rows were computed. With no running trips, `modes` is empty.

## Code

| File | What |
| --- | --- |
| `app.py` | FastAPI app, lifespan (pool), routes; no business logic |
| `models.py` | pydantic response models, response shaping and the health rule |
| `queries.py` | SQL only |
| `settings.py` | `BTP_*` settings and the reference time |
| `server.py` | console script `btp-metrics-api`: logging, then uvicorn |

## Tests

```sh
uv run pytest services/metrics-api                                # unit: shaping, health, settings
make up && uv run pytest -m integration services/metrics-api
```

The integration tests call the compose API (fixtures mode): bus and tram rows with plausible
values, the cache header and `/health`. They also check that `btp_api` can't read `events`
or `schedule` and can't create anything.
