# berlin-transit-pulse

[![CI](https://github.com/medhinioak/berlin-transit-pulse/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/medhinioak/berlin-transit-pulse/actions/workflows/ci.yml)

Live reliability metrics for Berlin public transport, built from VBB's open realtime feed.

BVG publishes per-line punctuality once a year. This project turns the live feed into a
continuously updated picture: on-time rate, delays, cancellations and headway regularity per
line, stop and hour, for buses, trams, the U-Bahn, the S-Bahn and regional trains in Berlin.

**Status (2026-11-20).** Milestone M1, a local walking skeleton, is nearly done: the pipeline runs
end to end on a developer machine and serves a read-only API. Nothing is deployed to AWS yet and
there is no public site. The first public release, v1.0, is targeted for 2027-01-17. Current
status: [`docs/plan.md`](docs/plan.md).

## What it answers

- Is my line reliable at the time I travel?
- Is it worse than usual right now?
- Which stop or hour is the problem?

Journalists, transit advocates and district politicians get comparable per-line and per-hour
numbers with a documented method. Scope and non-goals: [`docs/spec.md`](docs/spec.md).

## How it works

```text
VBB GTFS-RT feed, polled every 30 s
  └─► ingest (diffs snapshots, publishes changed trips)
        └─► Kafka topic trip-updates
              └─► stop-event-processor ─► Postgres, schema events
                                              └─► metrics-jobs ─► Postgres, schema marts (+ Parquet in S3)
                                                                      └─► metrics-api ─► CloudFront ─► browser
VBB static GTFS ─► schedule ─► Postgres, schema schedule ─► read by metrics-jobs and the API views
```

Five Python services, each in its own container with its own database role, talk through one
Kafka topic and a read-only API. The release runs on one small AWS server with Terraform, within
a budget of €20 per month. Full design, trade-offs and what is built versus planned:
[`docs/architecture.md`](docs/architecture.md).

| Area | Choice |
| --- | --- |
| Language | Python 3.12, managed with [uv](https://docs.astral.sh/uv/) |
| Streaming | Apache Kafka (single broker, KRaft) |
| Storage | Postgres 16; Parquet on S3 for history |
| API | FastAPI, read-only |
| Infrastructure | Docker Compose; Terraform on AWS (`eu-north-1`); GitHub Actions |
| Data | VBB GTFS-Realtime and static GTFS, CC BY 4.0 |

## Run it locally

You need Docker with Compose v2, [uv](https://docs.astral.sh/uv/getting-started/installation/)
(for example `brew install uv`; it provides Python 3.12) and `make`.

```sh
make install                       # every service and the dev tools into .venv
BTP_FEED_MODE=fixtures make up     # Kafka, Postgres and all services, offline: replays recorded snapshots
curl http://localhost:8000/v1/now/by-mode   # live indicator per mode; OpenAPI docs at /docs
make down                          # stop, keep data
```

The recorded snapshots are from 2026-11-18, so in fixtures mode the API pins "now" to that
rush hour. No network access and no API key are needed. Live mode calls the real VBB feed, needs
`BTP_FEED_CONTACT` and must follow the
[rules for using the feed](docs/data-sources.md#rules-for-using-the-feed).

```sh
make test                # unit tests with coverage
make test-integration    # needs `make up`
make lint                # ruff
make typecheck           # mypy, strict, per package
make help                # every target
```

Run `uv run pre-commit install` once to run ruff and the file checks on every commit. On macOS,
keep the clone outside iCloud-synced folders such as `~/Documents`
(see [troubleshooting](docs/troubleshooting.md)).

## Repository layout

| Path | Content |
| --- | --- |
| `services/` | `ingest`, `stop-event-processor`, `schedule`, `metrics-jobs`, `metrics-api` |
| `libs/common/` | Shared settings, logging and fixture loading |
| `schemas/` | Kafka message contracts |
| `fixtures/` | Recorded feed snapshots and a small static GTFS used by all tests |
| `deploy/` | Docker Compose stack and, from M2, Terraform |
| `web/` | Commuter UI (not started) |
| `docs/` | Documentation |

## Documentation

Start with the [documentation index](docs/README.md). The main documents:

| Document | Answers |
| --- | --- |
| [`docs/spec.md`](docs/spec.md) | What is it for, who for, what is in version 1.0? |
| [`docs/architecture.md`](docs/architecture.md) | How is it built, and why this way? |
| [`docs/plan.md`](docs/plan.md) | What ships when, and what is the status? |
| [`docs/data-sources.md`](docs/data-sources.md) | Where does the data come from, under which terms? |
| [`docs/cost.md`](docs/cost.md), [`docs/risks.md`](docs/risks.md) | What does it cost, and what could go wrong? |
| [`docs/adr/`](docs/adr/README.md) | Which decisions were made, and why? |
| [`docs/process.md`](docs/process.md), [`CONTRIBUTING.md`](CONTRIBUTING.md) | How do I work on it? |

## Data and attribution

Realtime and timetable data: [VBB Verkehrsverbund Berlin-Brandenburg GmbH](https://www.vbb.de/vbb-services/api-open-data/),
licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).

## Licence

The code and documentation are released under the [MIT licence](LICENSE). The VBB data keeps its
own licence, CC BY 4.0, as above.
