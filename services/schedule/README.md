# schedule

Imports VBB's static GTFS into the Postgres schema `schedule` as versions, and activates one
of them. Other services read only the `schedule.v_*` views, which show the active version
(one per table, plus `v_active_version`: its id, feed dates and activation time).

Data: VBB Verkehrsverbund Berlin-Brandenburg GmbH, CC-BY 4.0
(<https://creativecommons.org/licenses/by/4.0/>), from <https://www.vbb.de/vbbgtfs>.

## Commands

```sh
uv run btp-schedule import --activate        # download (cached) and import, then activate
uv run btp-schedule import --file feed.zip    # a zip or a folder of .txt files
uv run btp-schedule import --url URL
uv run btp-schedule activate VERSION_ID       # switch to a ready or retired version
uv run btp-schedule status                    # versions, feed dates, row counts
```

`import` with neither `--url` nor `--file` downloads `BTP_SCHEDULE_URL`, or, with
`BTP_FEED_MODE=fixtures`, imports `fixtures/gtfs-static/mini` without network.
Every command first applies `src/btp_schedule/sql/*.sql` (idempotent) as `btp_schedule`.

Imports hold a Postgres advisory lock, so two at once (compose and a manual run) are
serialised: the second waits, then finds the version already imported.

`--activate` activates a new or `ready` version. It never re-activates a `retired` one: after a
rollback with `btp-schedule activate <older>`, the next `make up` leaves the rollback in place.
To go forward again, run `btp-schedule activate <newer>` by hand.

Fixtures and live mode never share these versions: `make up` runs compose project
`btp-fixtures` and `BTP_FEED_MODE=live make up` runs `btp-live`, each with its own Postgres
volume (#75). So switching modes needs no re-activation. Each project keeps its own active
version, and the rule above stays as it is. Before #75 both modes used one volume, and a live
run left the mini version `retired` for good.

In compose, the one-shot container `schedule-import` runs `import --activate` on `make up` and
exits. `make up` waits for it. Services that read the schedule depend on it with
`condition: service_completed_successfully`.

| Variable | Default | Meaning |
| --- | --- | --- |
| `BTP_PG_HOST`, `BTP_PG_PORT`, `BTP_PG_DB` | `localhost`, `5432`, `btp` | Postgres |
| `BTP_PG_SCHEDULE_PASSWORD` | `dev_schedule` | password of `btp_schedule` |
| `BTP_SCHEDULE_URL` | `https://www.vbb.de/vbbgtfs` | the static GTFS zip |
| `BTP_SCHEDULE_CACHE_DIR` | `~/.cache/btp-schedule` (`/cache` in the image) | downloaded zip |
| `BTP_SCHEDULE_MAX_AGE_HOURS` | `24` | a cached zip younger than this is used without a request |
| `BTP_FEED_CONTACT` | the repo URL | contact in the User-Agent |
| `BTP_FEED_MODE` | `live` (compose: `fixtures`) | `fixtures` imports the mini GTFS |

VBB's download sends no ETag or Last-Modified, so the cache can't be revalidated cheaply. Every
request transfers the full ~84 MB. That's why a fresh cache is used without asking. A 304
(should VBB ever send validators) restarts the max-age window. If the download fails or returns
something that isn't a zip (an HTML error page, a truncated body), the cached zip is imported
and no `.part` file is left. Without a cache, that is an error.

## Versioning model

The decision and its alternatives are recorded in
[ADR-0009](../../docs/adr/0009-version-static-gtfs-and-swap-atomically.md).

- **Version id**: the first 12 hex characters of the zip's SHA-256 (for a folder, of its file
  names and contents). Importing the same zip again does nothing.
- **Tables**: `agency`, `routes`, `trips`, `stops`, `stop_times`, `calendar`, `calendar_dates`
  and `feed_info`. Each has `version_id` in its primary key. `versions` holds one row per
  version: sha256, source, download time, feed date range, row counts, status and failure reason.
- **Status**: `importing` → `ready` → `active` → `retired`, or `failed`. A partial unique index
  allows only one `active` version.
- **Import**: each file is streamed into Postgres with `COPY`. stop_times is never held in
  memory; peak RSS for the real feed is ~65 MB. Data and the `ready` status commit in one
  transaction, so a crash leaves no partial version.
- **Validation** before `ready`:
  - the required files are present (agency, routes, trips, stops, stop_times);
  - routes, trips, stops and stop_times have rows;
  - every `stop_times.trip_id` is in trips;
  - the feed date range covers today (Europe/Berlin). The range comes from `feed_info` or,
    since VBB has none, from calendar and calendar_dates.

  On failure the version becomes `failed` with the reason, its rows are rolled back, and the
  active version is untouched. Only the import's own `importing` row is ever marked `failed`. If
  the connection is lost and even that can't be recorded, the row stays `importing` and the
  next import retries it.
- **Activation** is one transaction: the active version becomes `retired` and the new one
  `active`. Views switch atomically. A retired version can be re-activated to roll back.
- **Times**: `arrival_time` and `departure_time` keep the GTFS text. `arrival_secs` and
  `departure_secs` are seconds since the start of the service day (`25:10:00` → 90600). They
  are never parsed as clock times. Turning them into timestamps needs the service date
  ("noon minus 12 h", M3).
- **Mode**: `routes.mode` comes from `btp_schedule.modes.mode_for_route_type`: 109 S-Bahn,
  400s U-Bahn, 700s and 200s bus, 900s tram, 100s regional, 1000s and 1200s ferry, plus the
  basic types 0–4. Rail replacement buses keep the rail line's name (e.g. `RE1`, `S41`) but
  have route type 700, so their mode is `bus`.
- **Size**: the real feed takes about 1 GB per version in Postgres, almost all of it
  `stop_times` and its primary key. Pruning retired versions is not built yet (M3, with the
  weekly refresh).

## Berlin filter

The rule and its alternatives are recorded in
[ADR-0017](../../docs/adr/0017-define-berlin-and-mode-from-stop-ids-and-route-types.md).

A trip is in Berlin (`trips.in_berlin`) if any of its stops has a `stop_id` starting with
`de:11000:`, Berlin's state code in the German stop-ID scheme (DHID). A route is in Berlin
(`routes.in_berlin`) if any of its trips is.

**Verified against the real feed** (zip `b6d49a90863e`, downloaded 2026-11-18):

| Mode | Routes in Berlin | All routes |
| --- | ---: | ---: |
| bus | 281 | 1,101 |
| sbahn | 47 | 51 |
| tram | 31 | 50 |
| regional | 28 | 74 |
| ubahn | 9 | 9 |
| ferry | 6 | 8 |

- 202,750 of 256,783 trips are in Berlin.
- **Spot checks**: U8, S41, M10 (tram and bus), 100 and RE1 are all in Berlin.
- **Outside Berlin**: no BVG route. The non-Berlin trams are Potsdam, Cottbus, Frankfurt (Oder),
  Brandenburg an der Havel and Strausberg. The four non-Berlin "S-Bahn" routes are DB Regio
  lines around Leipzig, Dresden and Magdeburg (S1, S4, S6 and an RE10 coded as 109).
- **Stops without a DHID**: 13,648 stops have none (e.g. `000300135061`). Most are entrances
  and generic nodes (location types 2 and 3). 341 are platforms, 29 of them in Berlin
  ("U Otisstr. (Berlin)", parent `de:11000:900086101`). Counting the parent station as well
  would change no trip and no route: every trip that stops at such a platform also has a
  `de:11000:` stop. Check this again after the timetable change on 2026-12-13.

## Known limitations (for #24, weekly refresh)

- **No pruning:** retired and failed versions are never deleted. At ~1 GB each, they have to be
  pruned before the weekly refresh runs for long.
- **Staging early:** validation needs the feed's date range to cover today. A zip published
  before its start date (e.g. the timetable starting on 2026-12-13) fails instead of becoming
  `ready`, and `make up` then fails because `schedule-import` exits 1. The active version stays
  in place.
- **Fixture expiry:** the mini fixture's calendar ends on 2027-12-11. After that,
  `BTP_FEED_MODE=fixtures` imports fail validation until the fixtures are re-recorded.

## Tests

```sh
uv run pytest services/schedule                       # unit
make up && uv run pytest -m integration services/schedule
```

The integration tests import zips built from the mini fixture. They cover:

- a re-import;
- a second version and switching;
- a rollback that `import --activate` must not undo;
- two concurrent imports of the same zip;
- a failed import.

They restore the version that was active before.
