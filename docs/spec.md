# Product specification

What berlin-transit-pulse is for, who it serves and what version 1.0 includes. Delivery is
tracked in [`plan.md`](plan.md), the design in [`architecture.md`](architecture.md). A change to
scope is made here, through a pull request.

## Problem

BVG publishes per-line punctuality only once a year, in answers to written questions from the
Berlin parliament. A commuter cannot see whether their line is reliably bad at 8 am, or whether
today is unusual. VBB now publishes live delay data openly, but as far as the research of
2026-11-18 found (not exhaustive), nobody turns it into a continuously updated reliability
picture for buses, trams and the U-Bahn. See [Prior art](#prior-art-and-gap).

## Users and the questions they ask

| Users | Questions the product answers |
| --- | --- |
| **Primary: Berlin commuters** | Is my line reliable at the time I travel? Is it worse than usual right now? Which stop or hour is the problem? |
| **Secondary: journalists, transit advocates, district politicians** | What are comparable per-line and per-hour numbers, and how were they calculated? |

## Version 1 scope

**Data and processing**

- Continuous ingestion of the VBB GTFS-Realtime feed every 30 s, with each update matched to
  its scheduled trip and stop ([data sources](data-sources.md)).
- Modes: bus, tram, U-Bahn, S-Bahn and regional rail, within Berlin. Lines that only run in
  Brandenburg are filtered out ([ADR-0017](adr/0017-define-berlin-and-mode-from-stop-ids-and-route-types.md)).
  S-Bahn was kept in scope on 2026-11-18 after the rush-hour feed check showed live delays for
  every mode (issue #1, [troubleshooting](troubleshooting.md)). Coverage per mode is monitored.

**Metrics**, per line, stop, hour of day and weekday type (weekday or weekend):

| Metric | Notes |
| --- | --- |
| On-time rate | Each operator's own definition (see below) |
| Average, median and p95 delay | In seconds, over stop events that have been observed |
| Cancellation rate | Share of scheduled trips cancelled |
| Headway regularity | For frequent lines only; the formula is part of the metric definitions |

The exact definitions (windows per operator, percentiles, coverage thresholds, the headway
formula) are fixed by the metric-definitions ADR (planned ADR-0005, milestone M3). Until it
exists, no definition is final. Two official punctuality definitions are the starting point:

- BVG counts a departure as on time between 90 s early and 210 s late.
- S-Bahn Berlin counts a train as punctual up to 5:59 min late.

**Measurement validity.** The feed carries predictions, not measured arrivals. An "observed"
stop event is the last prediction made before the stop time, after a grace window
(planned ADR-0004). The methodology page says so, and the figures are checked against outside
references before launch.

**Product surfaces**

- A **"Right now"** view: each line's current delay and cancellations compared with its normal
  for this hour.
- A **line page** with history: an hour-by-weekday heatmap and the trend over the weeks collected.
- A **network overview**.
- A public **degradation banner** when a line is clearly worse than its baseline.
- A **methodology page**: definitions, known data gaps, data sources and the CC-BY attribution.
- **Operational alerts** to the maintainers: feed stale, schedule match rate dropped, consumer
  lag, disk or memory pressure.

**Honesty about data.** Hours whose data coverage is below a threshold are shown as gaps,
never as values. A figure on the site always comes from the pipeline.

## Non-goals for v1

- Journey planning, departure boards, live vehicle maps (the feed has no vehicle positions).
- Delay prediction or machine learning.
- Push notifications, or accounts for public users.
- Brandenburg, other regions, multi-region deployment.
- High availability: one Kafka broker and one database, documented as such.
- A native mobile app. The website is mobile-first instead.

## Definition of done for v1.0

Milestone M5, tag `v1.0`. All items are verifiable:

- [ ] Public URL live, with at least 5 weeks of continuous history.
- [ ] Ingestion captured at least 95% of polls over the last 7 days, measured by the project's
      own monitoring.
- [ ] Metrics checked against BVG's published yearly figures and against spot checks with live
      departures.
- [ ] The whole stack rebuilt from zero with Terraform in under 30 minutes.
- [ ] CI runs unit tests, recorded-feed tests and data-quality checks on every push.
- [ ] Monthly AWS cost under €20, shown from Cost Explorer.
- [ ] README and at least 5 ADRs published.

## Constraints and assumptions

| Area | Constraint |
| --- | --- |
| Scope | One region: Berlin. One realtime source: VBB's official feed, joined with VBB's static GTFS |
| Data terms | CC BY 4.0; at most 7 requests per minute; contact details in the User-Agent ([data sources](data-sources.md)) |
| Budget | Release stack under €20 per month, on AWS, built with Terraform ([cost](cost.md)) |
| Capacity | One maintainer, part-time. Milestones have target dates and exit criteria, not weekly plans ([plan](plan.md)) |
| Target | v1.0 on 2027-01-17 with at least 5 weeks of collected history |
| Exit | When the live service is no longer affordable: shut down compute, keep publishing historical findings as a static site ([cost](cost.md#shutdown-and-archive-plan)) |

## Prior art and gap

Research on 2026-11-18, not exhaustive:

| Project | What it offers | Why it does not cover this |
| --- | --- | --- |
| stats.traines.eu (`traines-source/public-transport-statistics`) | Germany-wide delay distributions from archived GTFS-RT | Mostly focused on DB rail |
| Bahn-Vorhersage, bahn.expert | DB train delay archive and predictions | DB trains only |
| `piebro/deutsche-bahn-data` | DB delay statistics | DB trains only |
| FH Potsdam and Tagesspiegel (2016) | One-off visual analysis of BVG realtime data | A single analysis, not continuous |
| bvg.royli.dev, the BVG Live Timetable app | Live departures | No reliability history |
| BVG parliamentary answers | Per-line punctuality | Published only yearly |

Gap: no continuously updated, public per-line, per-stop and per-hour reliability dashboard for
BVG bus, tram and U-Bahn was found.
