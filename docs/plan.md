# Plan

The full planning document (architecture trade-offs, costs, risks, diagrams) lives in the
Claude project "Transport reliability tracker". This file is the working copy for the repo:
what Claude Code and contributors need to stay in scope. Changes to scope or milestones
are made here through a PR.

## Goal

A live, commuter-first reliability tracker for Berlin public transport. BVG publishes
per-line punctuality only once a year; this answers, continuously: is my line reliable at
the time I travel, is it worse than usual right now, and which stop or hour is the problem.
Secondary readers are journalists and transit advocates; hiring managers are reached
through the README and blog.

## Constraints

- Solo and part-time; work is sporadic, so milestones have target dates, not weekly plans.
- Cloud budget under €20/month for the release. AWS, Terraform.
- One region: Berlin. Data source: VBB's official GTFS-RT feed (CC-BY 4.0, 7 requests/min)
  joined with VBB's static GTFS.

## v1 scope

- Ingestion every 30 s; each update matched to its scheduled trip and stop.
- Per line, stop, hour of day and weekday type: on-time rate (each operator's own
  definition), average and p95 delay, cancellation rate, headway regularity for frequent lines.
- "Right now vs normal" view, line pages with history, network overview, degradation banner.
- Ops alerts: feed stale, match rate low, consumer lag, disk and memory.
- Methodology page with definitions, data gaps and attribution.
- Modes: bus, tram, U-Bahn, regional rail within Berlin; S-Bahn depends on feed coverage
  (decision issue in M1).

## Non-goals for v1

- Journey planning, departure boards, live vehicle maps (the feed has no positions).
- Delay prediction or ML.
- Push notifications or accounts for public users.
- Brandenburg and other regions.
- High availability: single broker, single database, documented as such.
- A native app (mobile-first website instead).

## Architecture (release)

One EC2 t4g.small running docker compose; S3 for raw snapshots, Parquet history and
backups; CloudFront for the web UI and API.

| Service | Owns | Reads from → writes to |
| --- | --- | --- |
| ingest-service | Polling, diffing, raw snapshots | VBB feed → Kafka `trip-updates`, S3 |
| stop-event-processor | Turning predictions into finalised stop events | Kafka → Postgres `events` |
| schedule-service | Static GTFS import and versions | VBB static zip → Postgres `schedule` |
| metrics-jobs | dbt models, data-quality checks, Parquet export | Postgres → Postgres `marts`, S3 |
| metrics-api | Read-only API | Postgres `marts` (read-only role) |
| web | Commuter UI | metrics-api via CloudFront |

Kafka: single broker in KRaft mode, topic `trip-updates` keyed by trip ID + service date,
6 partitions, 24 h retention. Only changed trips are published. Batch jobs run on systemd
timers. Monitoring: CloudWatch metrics and alarms, healthchecks.io for the poller.

Other deployment targets (same images): a microservices proof of concept on ECS Fargate +
MSK Serverless + RDS, applied for a few hours and destroyed; a Helm chart tested on kind in CI.
Neither is part of the release.

## Milestones

A milestone is done when its exit criteria are met, not when its date passes. Issues
for each milestone are in GitHub.

| Milestone | Release | Target | Exit criteria |
| --- | --- | --- | --- |
| M1 Walking skeleton | v0.1 | 2026-11-30 | `make up` runs poller → Kafka → consumer → Postgres → API → page locally; services each have their own folder, Dockerfile and tests; fixtures recorded; AWS account baseline done |
| M2 Collecting 24/7 in AWS | v0.2 | 2026-12-07 | Release stack applied with Terraform; CI/CD deploys via SSM; raw snapshots in S3; public URL with live counts; memory measured |
| M3 Trustworthy metrics | v0.3 | 2026-12-21 | Stop events finalised and tested on edge cases; GTFS refresh survives the 2026-12-13 timetable change; data-quality checks, status page, alarms; per-line metrics live; ADRs 1–5 |
| M4 Public beta | v0.9 | 2027-01-10 | dbt models with tests; Parquet export; commuter UI with line pages; methodology page |
| M5 v1.0 launch | v1.0 | 2027-01-17 | Metrics validated against outside references; rebuild from zero timed; cost under €20/month; README and ADR index |

Cut first if behind (in this order): stop-level metrics → headway regularity → degradation
banner → dbt (plain SQL instead). Never cut: 24/7 collection, data-quality checks, line pages.

Not on the critical path: alternative implementations (milestone "Alternatives", start
after M3) and the demo video and blog posts (milestone "After launch").

## Definition of done for v1.0

- [ ] Public URL live, with at least 5 weeks of continuous history
- [ ] Ingestion captured at least 95% of polls over the last 7 days, per the project's own monitoring
- [ ] Metrics checked against BVG's published yearly figures and spot checks against live departures
- [ ] Whole stack rebuilt from zero with Terraform in under 30 minutes
- [ ] CI runs unit tests, recorded-feed tests and data-quality checks on every push
- [ ] Monthly AWS cost under €20, shown from Cost Explorer
- [ ] README and at least 5 ADRs published
