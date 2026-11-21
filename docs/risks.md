# Risk register

What could go wrong, how likely it is and what the project does about it. The biggest risks
are gaps in the feed and the timetable change on 2026-12-13, not the infrastructure; both have
mitigations scheduled in M2 and M3. Review this page at every milestone gate
([process](process.md#milestone-gates)).

Likelihood is High, Medium or Low. Most ratings come from the project plan of 2026-11-18. Rows
added on 2026-11-20 from other project documents are the two "Present" data risks, the Free
plan, static GTFS disk growth and the single maintainer; their ratings are an assessment, to be
confirmed at the next milestone gate. "Present" means the problem has already been observed.

Last review: 2026-11-21, M1 gate (#14). The ratings of the rows added on 2026-11-20 are
confirmed. Added: fixtures mode and the timetable change.

## Data

| Risk | Likelihood | Mitigation |
| --- | --- | --- |
| **Feed outages or partial data.** VBB reports missing data since 2026-06-04, plus outages in February and March 2026 | High | Record coverage per hour and mode. Exclude hours below a coverage threshold from metrics and show the gaps in the UI. Subscribe to VBB's Atom news feed. Freshness alarm |
| **S-Bahn or DB-operated data incomplete** | Medium | S-Bahn was kept in scope after the rush-hour check on 2026-11-18 (#1). Per-mode coverage metric; show "insufficient data" rather than a misleading 100% on time |
| **Annual timetable change on 2026-12-13** (the current static GTFS runs to 12 December) breaks trip matching | High | Versioned GTFS import, rehearsed during M3 ([ADR-0009](adr/0009-version-static-gtfs-and-swap-atomically.md)). Match-rate alarm. Keep the previous version for late updates |
| **Late and duplicate events:** repeated snapshots, predictions changing after a stop is passed, trips vanishing before the last stop | High | Idempotent upserts keyed by (`trip_id`, `service_date`, `stop_sequence`). Finalise only after a grace window. Tests built from recorded snapshots |
| **Measurement validity.** "Observed" means the last prediction before the stop time, not a measured arrival | Medium | State it on the methodology page. Check against BVG's yearly figures and live departures. Record it in an ADR |
| **GTFS time quirks:** times past 24:00, service days, daylight-saving changes | Medium | Use the GTFS service date throughout. Unit tests for after-midnight trips and DST |
| **Schema or format changes:** new fields, a switch to differential mode, extensions | Low | Protobuf ignores unknown fields. Contract tests on fixtures. Alerts on unexpected entity counts or missing fields |
| **Fixtures mode breaks at the timetable change or on a mode switch.** The local stack, the integration tests and the CI smoke test replay 2026-11-18 data | Medium | Compose defaults every service to fixtures, with the mini GTFS that matches the fixtures (v0.1). Switching modes on one volume and failing loudly are #75 (M2). The CI smoke test catches an empty result on every PR |
| **Rate limiting or blocking** (7 requests per minute; generic User-Agents blocked) | Low | Poll twice a minute with ETag and gzip, User-Agent with contact details, reverse DNS on the Elastic IP, back off on 429 and 5xx |
| **Licensing (CC BY 4.0)** | Low | Attribution on every page and in the README. The methodology page names the sources. No public live mirror of the raw feed |
| **Realtime stop IDs missing from the static data** (about 1.3% of updates) | Present | Match by `stop_sequence` within the trip, not by `stop_id` ([troubleshooting](troubleshooting.md#realtime-stop_ids-that-arent-in-the-static-gtfs)) |
| **Trips that have not started or have finished carry zero delay** and distort shares | Present | Count only running or finished trips. The live view already does ([data sources](data-sources.md#behaviour-to-know-before-touching-the-data)) |

## Platform and cost

| Risk | Likelihood | Mitigation |
| --- | --- | --- |
| **Kafka and Postgres too big for 2 GB of RAM** | Medium | Measure at M2 (#20). Tuned heaps and 2 GB swap. Fallback: t4g.medium, or move Postgres off the box |
| **Instance or disk loss** | Low | Nightly `pg_dump` and Parquet in S3. Terraform rebuild in under 30 minutes |
| **Cost overrun** | Low | Budgets, anomaly detection, S3 lifecycle rules, no NAT gateway or load balancer ([cost](cost.md)) |
| **AWS Free plan ends** around mid-May 2027 or when credits run out, and some services may not be on it | Scheduled | Upgrade to the Paid plan before then. Check MSK availability before the proof-of-concept issue ([cost](cost.md#credits-and-the-free-plan)) |
| **Postgres disk fills with static GTFS versions** (about 1 GB each; pruning is not built) | Medium | Prune retired versions before the weekly refresh runs for long (#24) |
| **Proof-of-concept stack left running** | Medium | Separate Terraform state and tags, a budget alert for the run, an automatic scheduled destroy |

## Delivery and organisation

| Risk | Likelihood | Mitigation |
| --- | --- | --- |
| **Schedule slip** (holidays, limited capacity) | High | Collection goes live at M2. Cut list in the [plan](plan.md#cut-list). Exit criteria decide each milestone, not dates |
| **Single maintainer.** Knowledge and access sit with one part-time person | Medium | The documentation set, ADRs and issues with acceptance criteria are written to stand alone. All infrastructure is Terraform, with no console-only changes beyond the account baseline. A runbook arrives with M2. Open: how a second person gets AWS access ([`aws-account.md`](aws-account.md#access-for-additional-people)) |
| **Alternatives sprawl and rot,** delaying launch or reading as dead code | High | Start only after M3. Pick the top five first. Each must run with one command in CI on the same recorded fixtures, or it gets deleted ([alternatives](alternatives.md)) |
