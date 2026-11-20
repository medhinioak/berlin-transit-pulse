# Project plan: milestones and delivery

When each part of the product ships, what "done" means for each release and what is cut first
if time runs short. Scope is in [`spec.md`](spec.md), the design in
[`architecture.md`](architecture.md). Changes to scope or milestones are made here through a
pull request.

## How delivery is planned

- Work is organised in five milestones, M1 to M5, each a tagged release with exit criteria. A
  milestone is done when its criteria are met, not when its date passes. The dates are targets.
- Capacity is one part-time maintainer, so there are no weekly plans. Milestones, not
  sprints, are the planning unit ([process](process.md)).
- Only M2 is time-sensitive: every day earlier that collection starts is one more day of
  history at launch. v1.0 needs at least 5 weeks of continuous history.
- Tasks are GitHub issues, one GitHub milestone per release plus "Alternatives" and
  "After launch". **GitHub is the source of truth for status**; the table below is a snapshot.

## Status snapshot, 2026-11-20

| Milestone | Release | Target | Issues closed / open | State |
| --- | --- | --- | --- | --- |
| M1 Walking skeleton | v0.1 | 2026-11-30 | 15 / 3 | Open: #10 minimal web page, #13 Terraform state bucket, #14 tag v0.1 |
| M2 Collecting 24/7 in AWS | v0.2 | 2026-12-07 | 0 / 8 | Not started |
| M3 Trustworthy metrics | v0.3 | 2026-12-21 | 0 / 10 | Not started |
| M4 Public beta | v0.9 | 2027-01-10 | 0 / 8 | Not started |
| M5 v1.0 launch | v1.0 | 2027-01-17 | 0 / 6 | Not started |
| Alternatives (optional) | none | after M3 | 0 / 12 | Not started |
| After launch | none | after v1.0 | 0 / 4 | Not started |

## Milestones

### M1 Walking skeleton: v0.1, target 2026-11-30

A thin version of the whole pipeline runs locally.

- `make up` runs poller, Kafka, consumer, Postgres, API and one page showing the current average
  delay by mode (#10).
- Each service has its own folder, Dockerfile and tests (#2, #3, #5, #6, #7, #8, #9).
- Static GTFS import, and recorded feed snapshots as test fixtures (#4, #5).
- CI on every push (#11).
- AWS account baseline: MFA on root, admin user, budgets at $5, $15 and $20 (#12), and the
  Terraform state bucket (#13). See [`aws-account.md`](aws-account.md).

### M2 Collecting 24/7 in AWS: v0.2, target 2026-12-07

- Terraform release stack: EC2 with an Elastic IP and a reverse DNS name, S3 with lifecycle
  rules, IAM, SSM and CloudFront (#15, #16, #17).
- GitHub Actions: tests, arm64 images to GHCR, deploy through SSM (#18).
- Raw snapshots every 5 minutes to S3 (#19). Memory use measured on the real server (#20).
- Fixtures mode survives the timetable change (#75).
- Public URL showing live counts (#21).
- A runbook for deploy, roll back, restore and shut down.

### M3 Trustworthy metrics: v0.3, target 2026-12-21

- Stop events finalised with the grace window, cancellations and idempotent upserts, tested on
  recorded feeds including the edge cases (#22, #23).
- Static GTFS refresh survives the timetable change on 2026-12-13 (#24). Rehearse it beforehand
  if M2 is done in time.
- Data-quality checks and a status page (#25), CloudWatch alarms and consumer lag (#26),
  healthchecks.io (#27), nightly `pg_dump` (#28).
- Per-line on-time rate, p50 and p95 delay and cancellations live (#29).
- ADRs 1–5 written (#30). Tag v0.3 (#31).

### M4 Public beta: v0.9, target 2027-01-10

- dbt models for line by hour by weekday type, and stop level, with tests (#32).
- Headway regularity for frequent lines (#33).
- Nightly Parquet export to S3, partitioned by date and mode (#34).
- API history endpoints (#35).
- Commuter UI: line search and line page with "Right now vs normal" and the heatmap (#36),
  network overview and degradation banner (#37), methodology page with attribution and data
  gaps (#38).
- Tag v0.9 (#39).

### M5 v1.0 launch: v1.0, target 2027-01-17

- Metrics validated against BVG's yearly figures and live departures (#40).
- Rebuild from zero with Terraform, timed (#41).
- Cost review and CloudFront cache rules (#42).
- README and ADR index (#43), remaining ADRs 6–13 (#44).
- The [definition of done](spec.md#definition-of-done-for-v10) is met. Tag v1.0 (#45).

## Cut list

If the schedule slips, cut in this order: stop-level metrics, then headway regularity, then the
degradation banner, then dbt (fall back to plain SQL views).

**Never cut:** 24/7 collection, data-quality checks and the line page.

## Not on the critical path

- **Alternatives track** (GitHub milestone "Alternatives", #46–#57): optional implementations
  of the same pipeline on other technologies. Starts after M3, once there is real code to port.
  See [`alternatives.md`](alternatives.md).
- **After launch** (#58–#61): a short demo video and written findings. They do not gate v1.0.

## Content of the public launch material

Lead with what commuters learn, then show the engineering with real numbers: uptime, lag, cost
and what broke. Every figure comes from the pipeline, never from an estimate.

**README** (#43)

- One-line pitch, live link, a screenshot of a line page.
- The three commuter questions, with example answers from real data.
- Architecture diagram and a one-paragraph walk through the data path.
- Data sources and CC-BY attribution, with a link to the methodology page.
- How to run it locally: `docker compose up` replays recorded snapshots, so no network or API
  key is needed.
- Operations: what is monitored, alert thresholds, measured uptime and monthly cost.
- ADR index, known limitations and what a production version would change.
- Repository layout, tests, CI badge and licence.

**Demo video, about 3 minutes** (#58)

1. A commuter question answered on a real line page: "Right now vs normal" and the heatmap.
2. The data path live: a snapshot arriving, change events in Kafka, a finalised stop event in
   Postgres.
3. A simulated stale feed setting off the data-quality alarm and the email.
4. The whole stack rebuilt from zero with Terraform.

**Written findings**

1. For commuters, the main one (#59): which Berlin lines are reliable at rush hour, after N
   weeks of live data. Findings first, method linked.
2. For engineers (#60): turning a 10 MB snapshot every 30 seconds into a stream of changes
   (diffing, Kafka keys, late and duplicate events, idempotency, the memory budget on 2 GB).
3. Later (#61): a cost post, either the move from RDS to self-hosted Postgres (if credits are
   used) or archiving the live system into static history.

## Architecture decision records

ADRs 1–5 are written during M3 while the reasons are fresh, and the rest by M5. ADRs 14–16 wait
until the proof of concept is built. The list and its status are in [`adr/README.md`](adr/README.md).
