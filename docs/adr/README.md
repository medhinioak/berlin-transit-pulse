# Architecture Decision Records

One file per significant decision, numbered in order. Accepted ADRs are not edited, apart from
corrections to their metadata and links (the pull request says so); a changed decision gets a
new ADR that supersedes the old one. Start from [`0000-template.md`](0000-template.md). How and
when to write one: [`../process.md`](../process.md#writing-an-adr).

## Written

| ADR | Decision | Status |
| --- | --- | --- |
| [0009](0009-version-static-gtfs-and-swap-atomically.md) | Version the static GTFS and swap versions atomically | Accepted |
| [0017](0017-define-berlin-and-mode-from-stop-ids-and-route-types.md) | Define "in Berlin" by DHID state code and mode by route type | Accepted |
| [0018](0018-use-eu-north-1-and-an-iam-user-with-aws-login.md) | Use eu-north-1 and an IAM user with `aws login` on the Free plan | Accepted |
| [0019](0019-pin-the-reference-time-of-live-views-with-a-session-setting.md) | Pin the reference time of live views with a session setting | Accepted |
| [0020](0020-run-the-release-on-one-t4g-small-with-no-ssh.md) | Run the release on one t4g.small with no SSH | Accepted |

## Planned

Sixteen ADRs were planned up front (numbers 1–16, with 9 now written). Until each one is
written, the decision is already made and its rationale is in the document listed here. ADRs 1–5
are written in M3 (#30) and 6–13 by M5 (#44). ADRs 14–16 wait until the proof of concept is built.

| ADR | Decision | Rationale today | Written |
| --- | --- | --- | --- |
| 0001 | Use VBB's official GTFS-RT feed as the only realtime source (not the DELFI national feed or the unofficial wrappers) | [Data sources](../data-sources.md#sources) | M3 |
| 0002 | Run a single-broker Kafka (KRaft) on the same host, and what production would need instead | [Technology choices](../architecture.md#technology-choices), [Known limitations](../architecture.md#known-limitations) | M3 |
| 0003 | Publish changed trips, not full snapshots: event shape, keys, partitions, retention | [Topic contract](../../schemas/trip-updates.md), [Technology choices](../architecture.md#technology-choices) | M3 |
| 0004 | Define an observed stop event as the last prediction before the stop time, finalised after a grace window | [Architecture](../architecture.md#from-prediction-to-observed-stop-event-m3) | M3 |
| 0005 | Metric definitions: on-time windows per operator, delay percentiles, cancellation rate, headway regularity | [Spec](../spec.md#version-1-scope), [Data sources](../data-sources.md#official-punctuality-definitions) | M3 |
| 0006 | Self-host Postgres on the server (RDS, TimescaleDB and DynamoDB rejected) | [Technology choices](../architecture.md#technology-choices), [Cost](../cost.md#credits-and-the-free-plan) | M5 |
| 0007 | Keep history as Parquet on S3, queried with DuckDB (Iceberg, Athena and Redshift rejected) | [Technology choices](../architecture.md#technology-choices) | M5 |
| 0008 | Keep a raw snapshot every 5 minutes for 14 days, not every poll | [Data sources](../data-sources.md#data-volume-and-what-it-means-for-storage), [Cost](../cost.md#keeping-spend-low) | M5 |
| 0010 | Show feed gaps instead of filling them: coverage thresholds | [Risks](../risks.md#data), [Data quality](../architecture.md#data-quality) | M5 |
| 0011 | Orchestrate batch jobs with systemd timers (Airflow and Dagster rejected) | [Technology choices](../architecture.md#technology-choices) | M5 |
| 0012 | Deploy with Terraform, GitHub Actions and SSM, without SSH | [Technology choices](../architecture.md#technology-choices), [Security and access](../architecture.md#security-and-access) | M5 |
| 0013 | Cost ceiling and the shutdown and archive plan | [Cost](../cost.md#shutdown-and-archive-plan) | M5 |
| 0014 | Service boundaries: one schema and role per service in a shared Postgres | [Design rules](../architecture.md#design-rules), [Data stores](../architecture.md#postgres) | After the proof of concept |
| 0015 | Two deployment targets from the same images: compose for the release, ECS for the proof of concept | [Deployment targets](../architecture.md#deployment-targets) | After the proof of concept |
| 0016 | ECS Fargate for the cloud proof of concept, Kubernetes only locally | [Alternatives](../alternatives.md) | After the proof of concept |

ADRs that came up outside this list take the next free number after the planned ones (17
onwards).
