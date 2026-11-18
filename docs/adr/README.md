# Architecture Decision Records

One file per significant decision, numbered in order. Accepted ADRs are not edited;
a changed decision gets a new ADR that supersedes the old one.
Start from [`0000-template.md`](0000-template.md).

| ADR | Decision | Status |
| --- | --- | --- |
| [0009](0009-version-static-gtfs-and-swap-atomically.md) | Version the static GTFS and swap versions atomically | Proposed |
| [0017](0017-define-berlin-and-mode-from-stop-ids-and-route-types.md) | Define "in Berlin" by DHID state code and mode by route type | Proposed |

Planned ADRs, in the order they are expected (from the plan):

1. Use VBB's official GTFS-RT feed as the only realtime source
2. Run a single-broker Kafka (KRaft) on the same host
3. Publish changed trips, not full snapshots
4. Define an observed stop event as the last prediction before the stop time, finalised after a grace window
5. Metric definitions: on-time windows per operator, delay percentiles, cancellation rate, headway regularity
6. Self-host Postgres on the server (RDS, TimescaleDB and DynamoDB rejected)
7. Keep history as Parquet on S3, queried with DuckDB
8. Keep a raw snapshot every 5 minutes for 14 days
9. Version the static GTFS and swap versions atomically
10. Show feed gaps instead of filling them (coverage thresholds)
11. Orchestrate batch jobs with systemd timers
12. Deploy with Terraform, GitHub Actions and SSM, without SSH
13. Cost ceiling and the shutdown and archive plan
14. Service boundaries: one schema and role per service in a shared Postgres
15. Two deployment targets from the same images (compose for release, ECS for the proof of concept)
16. ECS Fargate for the cloud proof of concept, Kubernetes only locally

ADRs that came up outside the plan take the next free number after the planned ones (17 onwards).
