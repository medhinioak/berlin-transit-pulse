# Compose stack

`compose.yaml` runs the platform locally and, from M2, on the release server (t4g.small,
2 GB RAM, arm64). The services are added to the same file by their own issues.

```sh
make up            # start, wait until healthy and the schedule is imported
BTP_FEED_MODE=fixtures make up   # same, offline: schedule from fixtures/
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
| `schedule-import` | `services/schedule/Dockerfile` | Imports and activates the static GTFS, then exits 0. A no-op when the zip is unchanged. `BTP_FEED_MODE=fixtures` imports the mini GTFS offline (see `services/schedule/README.md`) |

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

Host ports bind to `127.0.0.1` only, so the release server never exposes Kafka or Postgres
on its public interface, whatever the security group says.

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

The services and the OS share the remaining ~700 MB. Check with `docker stats --no-stream`
after adding anything.

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
