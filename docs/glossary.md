# Glossary

Terms used across the documentation and the code.

## Transport and data

| Term | Meaning |
| --- | --- |
| **VBB** | Verkehrsverbund Berlin-Brandenburg, the transport association that publishes the open data used here |
| **BVG** | Berliner Verkehrsbetriebe, the Berlin operator of buses, trams and the U-Bahn |
| **S-Bahn, U-Bahn** | Berlin's suburban rail and underground networks. The S-Bahn is run by S-Bahn Berlin |
| **Mode** | Bus, tram, U-Bahn, S-Bahn, regional rail or ferry. Derived from the GTFS route type, not from the line name ([ADR-0017](adr/0017-define-berlin-and-mode-from-stop-ids-and-route-types.md)) |
| **GTFS** (static) | General Transit Feed Specification: the timetable as a zip of CSV files (routes, trips, stops, stop times, calendars) |
| **GTFS-Realtime (GTFS-RT)** | Protobuf feed of live updates that refer to GTFS trips and stops by ID |
| **TripUpdate, StopTimeUpdate** | GTFS-RT messages: the predicted delay or time of a trip and of each of its stops |
| **FULL_DATASET** | A GTFS-RT mode in which every snapshot holds the whole feed, not only changes |
| **Service date, service day** | The day a trip belongs to in the timetable. A trip after midnight still belongs to the previous service day |
| **`stop_sequence`** | The position of a stop within a trip. Used to match realtime to the schedule |
| **DHID** | The German stop-ID scheme. A `de:11000:` prefix means a stop in Berlin |
| **Timetable change** | The annual switch to a new timetable. Next: 2026-12-13. Static trip IDs change with it |

## Metrics

| Term | Meaning |
| --- | --- |
| **Delay** | Predicted time minus scheduled time, in seconds |
| **On-time rate** | Share of stop events within an operator's on-time window. The windows are fixed in planned ADR-0005 |
| **p50, p95** | Median and 95th-percentile delay |
| **Cancellation rate** | Share of scheduled trips cancelled |
| **Headway** | The time between consecutive vehicles on a line |
| **Headway regularity** | How evenly vehicles are spaced on a frequent line. The formula is fixed in planned ADR-0005 |
| **Observed stop event** | The last prediction made before a stop's scheduled time, finalised after a grace window (planned ADR-0004). It is not a measured arrival |
| **Grace window** | The wait after a stop time before its stop event is finalised, so late updates can still arrive |
| **Coverage** | The share of expected data actually received for an hour and mode. Hours below a threshold are shown as gaps |
| **Live indicator** | A "right now" figure such as the current delay by mode. It is not a reliability metric |

## Engineering

| Term | Meaning |
| --- | --- |
| **ADR** | Architecture Decision Record: one page recording a decision, its alternatives and consequences ([index](adr/README.md)) |
| **KRaft** | Kafka's built-in consensus mode that replaces ZooKeeper |
| **Tombstone** | A Kafka message with a null value, used here to mean "this trip left the feed" |
| **Idempotent** | Doing it twice gives the same result as doing it once. All writes here are |
| **Walking skeleton** | The thinnest end-to-end version of the system, built first (milestone M1) |
| **Marts** | The Postgres schema with the figures that the API serves |
| **Fixtures** | Recorded feed snapshots and a small static GTFS used by tests and by offline runs |
| **Parquet, DuckDB** | A columnar file format for the history in S3, and the embedded database used to query it |
| **dbt** | A tool that builds and tests SQL models; planned for M4 |
| **SSM Run Command** | AWS's way to run commands on a server without SSH |
| **OIDC** | The sign-in method GitHub Actions uses to get short-lived AWS credentials |
| **CC BY 4.0** | The licence of the VBB data: free to use with attribution |
