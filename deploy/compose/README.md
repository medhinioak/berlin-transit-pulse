# Compose stack

`compose.yaml` runs the platform locally and, from M2, on the release server (t4g.small,
2 GB RAM, arm64). The services are added to the same file by their own issues.

```sh
make up            # start offline (fixtures), wait until healthy and the schedule is imported
BTP_FEED_MODE=live make up   # same, but download the static GTFS and poll the live feed
make ps            # containers and health
make kafka-topics  # describe topics
make psql          # psql as the superuser
make logs          # follow logs
make down          # stop, keep data
```

## What runs

| Container | Image | Purpose |
| --- | --- | --- |
| `kafka` | `apache/kafka:4.4.0` | One broker in KRaft mode (broker and controller in one process, no ZooKeeper) |
| `kafka-init` | `apache/kafka:4.4.0` | Creates the topics, then exits 0. Safe to re-run (`--if-not-exists`) |
| `postgres` | `postgres:16.16` | Database `btp` with one schema per writing service |
| `schedule-import` | `services/schedule/Dockerfile` | Imports and activates the static GTFS, then exits 0. A no-op when the zip is unchanged. Imports the mini GTFS offline by default; `BTP_FEED_MODE=live` downloads the VBB static GTFS (see `services/schedule/README.md`) |
| `ingest` | `services/ingest/Dockerfile` | Polls the feed and publishes changed trips to `trip-updates` ([schemas/trip-updates.md](../../schemas/trip-updates.md)). Replays `fixtures/vbb-rt/$BTP_FIXTURE_SET` (default `rush-hour`) unless `BTP_FEED_MODE=live`. 256 MB limit, 30 s stop grace period |
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
| Kafka | `kafka:9092` | `localhost:9094` |
| Postgres | `postgres:5432` | `localhost:5432` |
| metrics-api | `metrics-api:8000` | `localhost:8000` (OpenAPI docs at `/docs`) |
| web | `web:8080` | `localhost:8080` (the page; `/api/` goes to metrics-api) |

Host ports bind to `127.0.0.1` only, so the release server never exposes Kafka, Postgres or
the API on its public interface, whatever the security group says. How the API reaches the
internet is decided with the release stack (M2).

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
`deploy/compose/.env` (gitignored; `.env.example` lists them). The release stack in M2
replaces them with real secrets.

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

## Resetting

The init SQL runs only when the Postgres volume is empty. To start from scratch (this deletes
all Kafka and Postgres data, and the cached GTFS zip in the `schedule-cache` volume):

```sh
docker compose -f deploy/compose/compose.yaml down -v
make up
```

## Service images

Each service gets `services/<name>/Dockerfile`, built from the repo root as context, when
its issue adds it to compose. `services/ingest/Dockerfile` is the reference pattern: a
multi-stage build with uv, dependencies in their own cached layer, a non-root user, and no
source code in the runtime image.

```sh
docker build -f services/ingest/Dockerfile -t btp-ingest .
```
