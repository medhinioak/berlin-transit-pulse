# Compose stack

`compose.yaml` runs the platform locally and on the release server (t4g.small, 2 GB RAM,
arm64). The services are added to the same file by their own issues. On the server,
`compose.release.yaml` is layered on top (see [Release override](#release-override)).

```sh
make up            # start offline (fixtures), wait until healthy and the schedule is imported
BTP_FEED_MODE=live make up   # same, but download the static GTFS and poll the live feed
make ps            # containers and health
make kafka-topics  # describe topics
make psql          # psql as the superuser
make logs          # follow logs
make down          # stop, keep data
```

Every target works on the stack of the mode in `BTP_FEED_MODE` (default `fixtures`), so live
mode needs it on each command: `BTP_FEED_MODE=live make logs`, `BTP_FEED_MODE=live make down`.

## Modes and projects

Each data mode is its own compose project, with its own Kafka, Postgres and cache volumes
(#75). A mode never sees the other's schedule versions or events.

| Command | Project | Data |
| --- | --- | --- |
| `make up` | `btp-fixtures` | replays `fixtures/vbb-rt/` against the mini GTFS; no network |
| `BTP_FEED_MODE=live make up` | `btp-live` | polls the VBB feed, imports VBB's current static GTFS |
| `scripts/smoke.sh` | `btp-smoke` | fixtures, removed again (`down -v`) at the end |
| release server ([`deploy.sh`](../release/deploy.sh)) | `btp-live` | live |

`compose.yaml` has no `name:`; the Makefile always passes `-p`
(`COMPOSE_PROJECT_NAME`, default `btp-$(BTP_FEED_MODE)`). By hand, pass it too:
`docker compose -p btp-fixtures -f deploy/compose/compose.yaml ps`.

All projects bind the same localhost ports, so only one runs at a time. `make up` refuses
while another one runs and names the command that stops it.

Switching modes needs no manual step:

```sh
make down && BTP_FEED_MODE=live make up   # fixtures -> live
BTP_FEED_MODE=live make down && make up   # live -> fixtures: the mini GTFS is still active
```

In fixtures mode, metrics-api checks that the replayed trips are in the active schedule and
reports it in `/health` if not ([metrics-api README](../../services/metrics-api/README.md#schedule-check-fixtures-mode)).

### Volumes from v0.1

Up to v0.1 there was one project, `btp`, for both modes. Its volumes (`btp_kafka-data`,
`btp_postgres-data`, `btp_schedule-cache`) are no longer used and nothing deletes them. To
remove them:

```sh
docker volume ls --filter name=btp_     # check what is there
docker compose -p btp -f deploy/compose/compose.yaml down -v
```

## What runs

| Container | Image | Purpose |
| --- | --- | --- |
| `kafka` | `apache/kafka:4.4.0` | One broker in KRaft mode (broker and controller in one process, no ZooKeeper) |
| `kafka-init` | `apache/kafka:4.4.0` | Creates the topics, then exits 0. Safe to re-run (`--if-not-exists`) |
| `postgres` | `postgres:16.16` | Database `btp` with one schema per writing service |
| `schedule-import` | `services/schedule/Dockerfile` | Imports and activates the static GTFS, then exits 0. A no-op when the zip is unchanged. Imports the mini GTFS offline by default; `BTP_FEED_MODE=live` downloads the VBB static GTFS (see `services/schedule/README.md`) |
| `ingest` | `services/ingest/Dockerfile` | Polls the feed and publishes changed trips to `trip-updates` ([schemas/trip-updates.md](../../schemas/trip-updates.md)). Replays `fixtures/vbb-rt/$BTP_FIXTURE_SET` (default `rush-hour`) unless `BTP_FEED_MODE=live`. 256 MB limit, 60 s stop grace period (a live request, the Kafka flushes and a raw snapshot upload) |
| `stop-event-processor` | `services/stop-event-processor/Dockerfile` | Consumes `trip-updates` and keeps the latest prediction per trip and stop in `events.trip_state` and `events.stop_predictions` as `btp_processor` ([README](../../services/stop-event-processor/README.md)). 256 MB limit |
| `metrics-jobs-apply` | `services/metrics-jobs/Dockerfile` | Creates or replaces the `marts` views as `btp_metrics_jobs`, then exits 0. Waits up to 60 s for the processor's tables ([README](../../services/metrics-jobs/README.md)) |
| `metrics-api` | `services/metrics-api/Dockerfile` | Read-only HTTP API on port 8000 as `btp_api` ([README](../../services/metrics-api/README.md)). In fixtures mode it pins "now" to the recording time (ADR-0019). 128 MB limit |
| `web` | `nginx:1.30.6-alpine` | Serves `web/` (the live page) on port 8080 and proxies `/api/` to `metrics-api:8000` ([`web/nginx.conf`](web/nginx.conf)). Resolves `metrics-api` per request, so it keeps running while the API is down (the page shows its error state). 64 MB limit |

Topics are created only by `kafka-init`. Auto-creation is off, so a misspelt topic name
fails instead of creating a new topic. `--if-not-exists` leaves an existing topic as it is:
changing partitions or retention here doesn't change a running broker. A topic change needs
an ADR and an explicit `kafka-configs.sh --alter` (or `kafka-topics.sh --alter` for partitions)
on every stack.

| Topic | Partitions | Replication | Retention | Cleanup |
| --- | --- | --- | --- | --- |
| `trip-updates` | 6 | 1 | 24 h (`retention.ms=86400000`) | delete |

## Ports

| What | Inside the compose network | From the host |
| --- | --- | --- |
| Kafka | `kafka:9092` | `127.0.0.1:9094` (not `localhost`: IPv4 only) |
| Postgres | `postgres:5432` | `localhost:5432` |
| metrics-api | `metrics-api:8000` | `localhost:8000` (OpenAPI docs at `/docs`) |
| web | `web:8080` | `localhost:8080` (the page; `/api/` goes to metrics-api) |

Host ports bind to `127.0.0.1` only, so the release server never exposes Kafka, Postgres or
the API on its public interface, whatever the security group says. The one exception is the
release override, which also publishes nginx on port 80 for CloudFront (#17).

## Schemas and roles

Created by `postgres/init/01_schemas_roles.sql`.

| Role | Used by | Owns (create, write) | Can read |
| --- | --- | --- | --- |
| `btp_processor` | stop-event-processor | `events` | `events` |
| `btp_schedule` | schedule | `schedule` | `schedule` |
| `btp_metrics_jobs` | metrics-jobs | `marts` | `marts`, `events`, `schedule` |
| `btp_api` | metrics-api | nothing | `marts` |

Nobody except the superuser can create objects in `public`. Read access covers tables and
views created later too, through default privileges on the owning role. That means each
schema's tables must be created by its owning role, not by the superuser. ingest has no role
because it doesn't use Postgres.

## Passwords

The roles and the superuser use development defaults (`dev_processor`, `dev_api`, …) set in
`compose.yaml`. To override them, set the `BTP_PG_*_PASSWORD` variables in
`deploy/compose/.env` (gitignored; `.env.example` lists them). On the release server the
deploy renders `/opt/btp/.env` from SSM Parameter Store instead (passwords generated by
Terraform, [runbook](../../docs/runbook.md)).

## Memory

Budgeted for the 2 GB release server:

| Container | Limit | Notes |
| --- | --- | --- |
| `kafka` | 768 MB | heap `-Xms384m -Xmx512m`; the healthcheck CLI runs with `-Xmx64m` and only once a minute after start-up (each check is a JVM start: ~1.5 s CPU, ~+85 MB) |
| `postgres` | 512 MB | `shared_buffers=128MB`, `shm_size: 128m` (counts toward the limit when used) |
| `kafka-init` | 256 MB | runs once at start-up, then exits |
| `schedule-import` | 384 MB | runs once at start-up; peak ~65 MB on the real feed (COPY streaming), Postgres peaks ~210 MB meanwhile |
| `ingest` | 256 MB | ~33 MB in fixtures mode |
| `stop-event-processor` | 256 MB | ~46 MB in fixtures mode; librdkafka prefetch capped at 16 MB (`queued.max.messages.kbytes`) |
| `metrics-jobs-apply` | 128 MB | runs once at start-up, then exits |
| `metrics-api` | 128 MB | ~48 MB in fixtures mode (uvicorn, one worker, pool of up to 4 connections); lowered from 192 MB to make room for `web` (#10) |
| `web` | 64 MB | ~9 MB (nginx, one worker) |

The limits of the long-running containers (kafka, postgres, ingest, stop-event-processor,
metrics-api, web) add up to 1,984 MB, which leaves almost nothing for the OS if all of them reached
their limits at once. The limits are ceilings, not use; actual use is far lower (below), but
the next service has to come with lower limits elsewhere or a measured case for
overcommitting. Measured
use on 2026-11-19 (fixtures mode, #8) was ~700 MB in total: kafka ~550, postgres ~64, ingest ~33,
processor ~46. With metrics-api (#9, 2026-11-19): ~800 MB in total, kafka ~568, postgres ~110,
metrics-api ~48. With web (#10, 2026-11-20): web ~9 MB. A new service needs a limit that still fits next to these; check with
`docker stats --no-stream` after adding anything.

### Measuring memory

`scripts/measure_memory.sh` records every container of one compose project until it's
stopped, locally or on the release server (#77, #20):

```sh
scripts/measure_memory.sh /tmp/live.csv            # every 30 s, project btp-live
scripts/measure_memory.sh /tmp/fx.csv 5 btp-fixtures
nohup scripts/measure_memory.sh /tmp/live.csv >/tmp/measure.log 2>&1 &   # for hours
kill -TERM <pid>                                    # or Ctrl-C: writes the summary, exits 0
uv run python scripts/summarise_memory.py /tmp/live.csv --summary /tmp/live.summary.txt
```

- `live.csv` gets one row per running container per reading:
  `timestamp,container,mem_bytes,mem_limit_bytes,cpu_percent` (UTC). A restarted run appends.
  Each `docker stats` call takes about 2 s, so readings come a little slower than the interval.
- `live.summary.txt`, written on stop: each container's cgroup `memory.peak` (since the
  container started) and its `OOMKilled` flag. Containers that have exited are listed as skipped.
- The summariser prints a markdown table: median, p95, peak and time of peak (UTC and Berlin)
  per container. It needs Python 3.12 (stdlib only); the release server's `python3` is older,
  so copy the CSV and summary off the server and summarise them locally.
- `mem_bytes` is what `docker stats` shows. On cgroup v2 that leaves out inactive page cache;
  `memory.peak` includes it, so it's the higher, safer figure to set a limit from.
- Keep the raw CSVs out of the repo; post the table on the issue instead.

## Resetting

The init SQL runs only when the Postgres volume is empty. To start one mode from scratch (this
deletes that project's Kafka and Postgres data, and the cached GTFS zip in its
`schedule-cache` volume; the other mode is untouched):

```sh
docker compose -p btp-fixtures -f deploy/compose/compose.yaml down -v && make up
docker compose -p btp-live -f deploy/compose/compose.yaml down -v && BTP_FEED_MODE=live make up
```

## Release override

`compose.release.yaml` is used only on the release server, by
[`deploy/release/deploy.sh`](../release/deploy.sh), as compose project `btp-live`:

```sh
BTP_IMAGE_TAG=<sha> docker compose -p btp-live --env-file /opt/btp/.env \
  -f compose.yaml -f compose.release.yaml up -d --no-build
```

- The five service images come from GHCR (`ghcr.io/medhinioak/btp-<service>:<sha>`, arm64,
  built by `.github/workflows/release.yml`). They use `pull_policy: always`, and `build` is
  removed with `!reset`, so the server never builds an image.
- `BTP_FEED_MODE=live` for `schedule-import`, `ingest` and `metrics-api`.
- `schedule-import` exits 0 after a failed import if a version is already active, so a
  timetable published before its start date doesn't stop deploys. The deploy shows a warning
  ([runbook](../../docs/runbook.md#13-december-2026-the-timetable-change)).
- `web` also publishes `80:8080` on all interfaces. The security group admits only
  CloudFront's origin-facing ranges.

To check that it renders:

```sh
BTP_IMAGE_TAG=0000000000000000000000000000000000000000 BTP_RAW_BUCKET=example \
  docker compose -p btp-live \
  -f deploy/compose/compose.yaml -f deploy/compose/compose.release.yaml config
```

Operating the server: [`docs/runbook.md`](../../docs/runbook.md).

## Service images

Each service gets `services/<name>/Dockerfile`, built from the repo root as context, when
its issue adds it to compose. `services/ingest/Dockerfile` is the reference pattern: a
multi-stage build with uv, dependencies in their own cached layer, a non-root user, and no
source code in the runtime image.

```sh
docker build -f services/ingest/Dockerfile -t btp-ingest .
```
