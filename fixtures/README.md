# Fixtures

Recorded VBB feed data for tests and `BTP_FEED_MODE=fixtures`. Tests and CI never call the
live feed; they use these files. Load them with `btp_common.fixtures`:
`load_rt_fixture("rush-hour")` returns the snapshots as `FeedMessage`s and `mini_gtfs_dir()`
returns the matching static GTFS folder.

Data: VBB Verkehrsverbund Berlin-Brandenburg GmbH, CC-BY 4.0
(<https://creativecommons.org/licenses/by/4.0/>). Realtime from
<https://production.gtfsrt.vbb.de/data>, static GTFS from <https://www.vbb.de/vbbgtfs>.

## What's here

| Path | Contents |
| --- | --- |
| `vbb-rt/rush-hour/` | 4 consecutive trimmed snapshots, 30 s apart, recorded on Wed 2026-11-18 at 18:03–18:05 CET |
| `vbb-rt/evening/` | 4 consecutive trimmed snapshots, 30 s apart, recorded after 21:00 CET |
| `vbb-rt/<set>/NN.pb.gz` | A valid gzipped `FeedMessage`: the original header, only the chosen trips |
| `vbb-rt/<set>/manifest.json` | Per snapshot: feed timestamp, ETag, entity counts and sizes (full and trimmed); trim parameters; picks per category and mode; the edge-case trips by `trip_id@start_date` |
| `gtfs-static/mini/*.txt` | Plain-CSV GTFS with exactly the rows the realtime fixtures reference: their trips, all their stop_times, those stops and their parent stations, routes, agencies, calendar and calendar_dates |
| `gtfs-static/manifest.json` | SHA-256 and download time of the source zip, row counts, and the realtime stop_ids missing from the static stops (see below) |
| `local/` | Full snapshots and the static zip (~2.4 MB and 84 MB). Ignored by Git |

## How trips are chosen

A full snapshot has ~9,900 trips, and most of them haven't started yet. Their zero delay isn't
a measurement, so the trim script (`scripts/trim_fixtures.py`) picks mostly running trips.
Trips are keyed by `(trip_id, start_date)` and placed in one category from their phase in
each snapshot:

| Category | Meaning | Picks |
| --- | --- | --- |
| `running` | running in every snapshot | 25 per mode |
| `future` | not started in any snapshot | 3 per mode |
| `cancelled` | `CANCELED` in any snapshot | 8 |
| `starts` / `ends` | starts or finishes between the first and last snapshot | 6 each |
| `appears` / `disappears` | missing from the first or the last snapshot | 6 each |

Modes come from the route type (109 S-Bahn, 400s U-Bahn, 700s bus, 900s tram, other 100s
regional). These sets contain no ferry trips. Picks within a category are ordered by a
seeded SHA-256 of the key, so the same input always gives the same fixtures. The quotas keep
everything committed here under the 2 MB budget; `stops.txt` and `stop_times.txt` take most of it.

**Known quirk, kept on purpose:** about 1.3% of stop-time updates (15% of trips, including
S-Bahn) use a bare stop number such as `900162001` instead of a static `stop_id` like
`de:11000:900162001::1`. Some don't match any static stop. Match realtime to static by
`stop_sequence`, not by `stop_id`. See `docs/troubleshooting.md`.

## Re-recording

This calls the live feed: one request every 30 s, never faster than every 10 s. Set a
contact that VBB can reach (the repo URL is fine):

```sh
export BTP_FEED_CONTACT=https://github.com/medhinioak/berlin-transit-pulse
make fixtures LABEL=rush-hour   # weekday 07:00–09:00 or 16:30–18:30 Berlin time
make fixtures LABEL=evening     # after 21:00
```

`make fixtures` records 4 snapshots into `fixtures/local/vbb-rt/<label>/` (replacing an
earlier run), trims them into `fixtures/vbb-rt/<label>/`, then rebuilds `gtfs-static/mini`
from the cached zip in `fixtures/local/gtfs-static/`. If the cache is missing, it downloads
the zip first. Delete the cached zip after a timetable change (next one: 2026-12-13) so
realtime trip IDs match again. Then check `make test`: the consistency and budget tests run
on whatever is committed.
