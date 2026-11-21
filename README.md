# berlin-transit-pulse

[![CI](https://github.com/medhinioak/berlin-transit-pulse/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/medhinioak/berlin-transit-pulse/actions/workflows/ci.yml)

Live reliability metrics for Berlin public transport, built from VBB's open realtime feed.

BVG publishes per-line punctuality once a year. This project turns the live feed into a
continuously updated picture: on-time rate, delays, cancellations and headway regularity per
line, stop and hour, for buses, trams, the U-Bahn, the S-Bahn and regional trains in Berlin.

**Status: [v0.1](https://github.com/medhinioak/berlin-transit-pulse/releases/tag/v0.1), local walking skeleton (2026-11-21).** The pipeline runs end to end on a
developer machine, from recorded feed snapshots to a read-only API and one page showing the
current delay by mode. It is a live indicator, not yet a punctuality rate. Nothing is deployed
to AWS yet and there is no public site. The first public release, v1.0, is targeted for
2027-01-17. Current status: [`docs/plan.md`](docs/plan.md).

## What it answers

- Is my line reliable at the time I travel?
- Is it worse than usual right now?
- Which stop or hour is the problem?

Journalists, transit advocates and district politicians get comparable per-line and per-hour
numbers with a documented method. Scope and non-goals: [`docs/spec.md`](docs/spec.md).

## How it works

```text
VBB GTFS-RT feed, polled every 30 s (or recorded snapshots)
  └─► ingest (diffs snapshots, publishes changed trips)
        └─► Kafka topic trip-updates
              └─► stop-event-processor ─► Postgres, schema events
                                              └─► metrics-jobs ─► Postgres, schema marts (views)
                                                                      └─► metrics-api ─► web page
VBB static GTFS ─► schedule ─► Postgres, schema schedule ─► read by metrics-jobs and the API views
```

Five Python services, each in its own container with its own database role, talk through one
Kafka topic and a read-only API. In v0.1 all of it runs in Docker Compose on a laptop; marts are
SQL views over the live state. Planned, not built yet: the AWS release server with Terraform
within €20 per month and CloudFront in front (M2), finalised stop events and reliability metrics
(M3), and Parquet history in S3 (M4). Full design, trade-offs and what is built versus planned:
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
make install    # every service and the dev tools into .venv
make up         # Kafka, Postgres and all services, offline: replays recorded snapshots
open http://localhost:8080   # the live page (or visit it in a browser)
```

The API is at http://localhost:8000 (`/v1/now/by-mode`, OpenAPI docs at `/docs`). `make down`
stops the stack and keeps its data. The first `make up` builds the images, so it takes a few
minutes; values appear a few seconds after it returns.

The recorded snapshots are from 2026-11-18, so in fixtures mode the API pins "now" to that
rush hour. No network access and no API key are needed. `BTP_FEED_MODE=live make up` calls the
real VBB feed, needs `BTP_FEED_CONTACT` and must follow the
[rules for using the feed](docs/data-sources.md#rules-for-using-the-feed).

```sh
make test                # unit tests with coverage
make test-integration    # needs `make up`
scripts/smoke.sh         # end to end in its own compose project; needs curl and jq (stop `make up` first)
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
| `deploy/` | Docker Compose stack; Terraform (state bucket now, release stack from M2) |
| `web/` | Commuter UI (minimal live page, http://localhost:8080 after `make up`) |
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
