# Data sources, licensing and feed behaviour

Everything the pipeline reads comes from outside the project, so this page records what the
sources are, under which terms they may be used, how they behave and how they were measured.
Measurements are dated; check them again before relying on them.

## Sources

| Source | Format | Licence | Limits | Role |
| --- | --- | --- | --- | --- |
| **VBB realtime feed** `https://production.gtfsrt.vbb.de/data` | GTFS-RT TripUpdates, full dataset, same IDs as VBB's static GTFS | CC BY 4.0 | 7 requests per minute since 2026-11-02; a descriptive User-Agent with contact details is required, generic ones may be blocked; compression requested | **Primary realtime source** |
| **VBB static GTFS** `https://www.vbb.de/vbbgtfs` | GTFS zip, republished regularly (weekly per VBB's page, every few weeks in practice) | CC BY 4.0 | No limit documented. No ETag or Last-Modified, so every download is the full ~84 MB | Schedule join: lines, modes, stops, Berlin flag |
| DELFI Germany-wide GTFS-RT (official via opendata-oepnv.de; a public mirror exists at stc.traines.eu) | GTFS-RT | Static data: attribution required. Realtime terms not confirmed | Unknown | Fallback. Needs DELFI's own static GTFS, because its IDs differ from VBB's |
| HAFAS wrappers (`v6.vbb.transport.rest`, `v6.bvg.transport.rest`) | JSON departures per stop | Unofficial wrapper of an app API | 100 requests per minute | Not viable as a primary source: it cannot cover the whole network. Usable for spot checks |

The choice of the VBB feed as the only realtime source is recorded as planned ADR-0001.

Official data page: <https://www.vbb.de/vbb-services/api-open-data/>. VBB announces outages and
rate-limit changes in the feed's Atom news feed:
<https://production.gtfsrt.vbb.de/news.atom.xml>.

## Attribution

Licence CC BY 4.0 requires attribution. Every public page, the API responses and the README carry:

> Data: VBB Verkehrsverbund Berlin-Brandenburg GmbH, CC-BY 4.0

The project does not publish a live mirror of the raw feed. The methodology page names the
sources. Licence text: <https://creativecommons.org/licenses/by/4.0/>.

## Rules for using the feed

| Rule | Why |
| --- | --- |
| Never more than 7 requests per minute. The ingest service polls every 30 s and enforces at most 6 requests in any 60 s, retries included | VBB's published limit |
| Send `Accept-Encoding: gzip` and `If-None-Match` with the last ETag | VBB asks for compression; a 304 saves bandwidth for both sides |
| User-Agent `berlin-transit-pulse/<version> (+<contact>)`; no contact, no start in live mode | VBB blocks generic clients |
| Back off on 429, 5xx and network errors: 30 s, doubling up to 5 min; honour `Retry-After` | Politeness and recovery |
| Tests and CI never call the live feed; they use recorded snapshots in `fixtures/` | Reproducible tests, no load on VBB |
| Recording new fixtures: one request every 30 s, never faster than every 10 s | Same reason |

Implementation and tests of these rules: [`services/ingest/README.md`](../services/ingest/README.md).

## What the realtime feed contains

Measured on 2026-11-18 at 11:37 (3 polls) and re-checked at rush hour the same day:

| Property | Value |
| --- | --- |
| Content | `TripUpdates` only. No vehicle positions, no alerts |
| Mode | `FULL_DATASET`: every snapshot holds the whole feed |
| Latency | HTTP 200 in about 0.5–0.8 s; header timestamp 4–17 s old |
| Size | About 2.3 MB gzipped on the wire, 10.5 MB uncompressed |
| Volume | About 10,200 trip updates and 206,000 stop-time updates per snapshot |
| Coverage | All of VBB, including Brandenburg, so a Berlin filter is needed ([ADR-0017](adr/0017-define-berlin-and-mode-from-stop-ids-and-route-types.md)) |
| Cancellations | About 120 cancelled trips in the snapshot |
| Static GTFS size | 1,293 routes and about 257,000 trips on 2026-11-18, 5.7 M stop_times |

### Behaviour to know before touching the data

- **The feed includes trips that have not started and trips that have finished.** A zero delay
  on those is not a measurement. Shares computed over all trips in a snapshot are meaningless;
  count only trips that are running or have run. Example: the feed lists most of the day's
  S-Bahn trips in advance (about 2,945 future S-Bahn trips against 36 for the U-Bahn at 17:25),
  so a share over the whole feed made S-Bahn look broken. Counting only running trips at rush
  hour (2026-11-18, about 17:25), 155 S-Bahn trips were running, 38–39% with a non-zero delay,
  p95 6 min, maximum 14 min.
- **Some stop IDs do not match the static data.** About 1.3% of stop-time updates (15% of
  trips, including S-Bahn) carry a bare stop number instead of the static `stop_id` such as
  `de:11000:900162001::1`. Some match no static stop at all. Match realtime to static by
  `stop_sequence` within the trip, not by `stop_id`.
- **Trip identity.** `FeedEntity.id` changes whenever a trip's content changes, so it cannot
  identify a trip. The key is `trip_id` plus `start_date`.
- **Extended route types** in the static data: 109 S-Bahn, 400s U-Bahn, 700s bus, 900s tram,
  100s regional rail, 1000s ferry.
- **GTFS times** can exceed 24:00 (a trip after midnight belongs to the previous service day).
- **Header age varies.** A first poll at night on 2026-11-19 returned a header 448 s old. See
  [troubleshooting](troubleshooting.md#feed-header-several-minutes-old-at-night).

### Known upstream problems

- VBB states the feed has been "lacking some data since 2026-06-04", with no estimate for a
  fix, and had outages in February and March 2026. Which parts are missing is not stated.
  Live delays were present for every mode on 2026-11-18. The pipeline therefore records
  coverage per hour and mode and shows gaps instead of values ([risks](risks.md)).
- The rate limit was lowered to 7 requests per minute (announced 2026-10-27, effective
  2026-11-02).
- **Annual timetable change on 2026-12-13.** The static GTFS version changes and trip IDs
  change with it. The schedule import is built to survive it ([ADR-0009](adr/0009-version-static-gtfs-and-swap-atomically.md)).

## Data volume and what it means for storage

- Archiving every snapshot at 30 s would be about 6.6 GB per day, roughly 200 GB per month
  compressed. The project does not do this.
- Instead: a raw snapshot every 5 minutes, deleted after 14 days, plus compact derived
  Parquet events.
- Ingest does not publish each stop update (about 206,000 per poll). It publishes only trips
  that changed, which cuts Kafka traffic to a few thousand messages per poll
  ([topic contract](../schemas/trip-updates.md)).

## Official punctuality definitions

| Operator | On time means |
| --- | --- |
| BVG | Departure between 90 s early and 210 s late |
| S-Bahn Berlin | Up to 5:59 min late |

Source: the BVG definition from a Berlin parliament written question (19/15670). The project's
own definitions are fixed in the metric-definitions ADR (planned ADR-0005).

## Sources of this page

VBB GTFS-Realtime feed page (licence, rate limit, data notice, news feed); VBB open datasets
(static GTFS); BVG punctuality definition from Berlin parliament written question 19/15670;
the project's own measurement scripts, run on 2026-11-18.
