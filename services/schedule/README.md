# schedule

Imports VBB's static GTFS into the Postgres schema `schedule` as versions, and activates one
of them. Other services read only the `schedule.v_*` views:

- one per table showing the active version, plus `v_active_version` (its id, feed dates and
  activation time), for "now";
- `v_trips_by_service_date`, `v_routes_by_service_date` and `v_stop_times_by_service_date`,
  plus `version_for_service_date(d)`, for the version of a given service day (see
  [below](#staging-activation-and-pruning)).

On the release server two systemd timers run it daily (ADR-0011): `refresh` at 03:30 and
`activate-due` at 23:40 Europe/Berlin.

Data: VBB Verkehrsverbund Berlin-Brandenburg GmbH, CC-BY 4.0
(<https://creativecommons.org/licenses/by/4.0/>), from <https://www.vbb.de/vbbgtfs>.

## Commands

```sh
uv run btp-schedule import --activate        # download (cached) and import, then activate
uv run btp-schedule import --file feed.zip    # a zip or a folder of .txt files
uv run btp-schedule import --url URL
uv run btp-schedule refresh [--file|--url]    # the daily job: import, back up, catch up, prune
uv run btp-schedule activate VERSION_ID       # switch to a ready or retired version
uv run btp-schedule activate-due              # activate a staged version due tomorrow (the eve)
uv run btp-schedule status                    # versions, feed dates, activate_on, row counts
```

`import` with neither `--url` nor `--file` downloads `BTP_SCHEDULE_URL`, or, with
`BTP_FEED_MODE=fixtures`, imports `fixtures/gtfs-static/mini` without network.
Every command first applies `src/btp_schedule/sql/*.sql` (idempotent) as `btp_schedule`.

Imports hold a Postgres advisory lock, so two at once (compose and a manual run) are
serialised: the second waits, then finds the version already imported.

`--activate` activates a new or `ready` version, unless it is staged for a later day
(`activate_on` after today). It never re-activates a `retired` one: after a
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
| `BTP_RAW_BUCKET` | unset (off) | data bucket for zip backups (`backups/gtfs/`); release: SSM `data_bucket` |
| `BTP_AWS_REGION` | `eu-north-1` | region of that bucket |

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
  - the feed hasn't ended, and starts no more than 21 days after today (Europe/Berlin), so a
    timetable published ahead of its start can be staged. The range comes from `feed_info`
    or, since VBB has none, from calendar and calendar_dates.

  On failure the version becomes `failed` with the reason, its rows are rolled back, and the
  active version is untouched. Only the import's own `importing` row is ever marked `failed`. If
  the connection is lost and even that can't be recorded, the row stays `importing` and the
  next import retries it.
- **Activation** is one transaction: the active version becomes `retired` and the new one
  `active`, and the change is recorded in `version_activations`. Views switch atomically. A
  retired version can be re-activated to roll back.
- **Times**: `arrival_time` and `departure_time` keep the GTFS text. `arrival_secs` and
  `departure_secs` are seconds since the start of the service day (`25:10:00` → 90600). They
  are never parsed as clock times. Turning them into timestamps needs the service date
  ("noon minus 12 h", M3).
- **Mode**: `routes.mode` comes from `btp_schedule.modes.mode_for_route_type`: 109 S-Bahn,
  400s U-Bahn, 700s and 200s bus, 900s tram, 100s regional, 1000s and 1200s ferry, plus the
  basic types 0–4. Rail replacement buses keep the rail line's name (e.g. `RE1`, `S41`) but
  have route type 700, so their mode is `bus`.
- **Size**: the real feed takes about 1 GB per version in Postgres, almost all of it
  `stop_times` and its primary key. Each refresh prunes old versions (below).

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

## Staging, activation and pruning

The decision and its alternatives are recorded in
[ADR-0023](../../docs/adr/0023-stage-record-and-prune-schedule-versions.md), which extends
ADR-0009.

- **Staging**: a feed starting up to 21 days after today imports as `ready` with
  `activate_on` = its start date. A feed starting further ahead is refused before loading:
  only feed_info, calendar and calendar_dates are read. The command logs the date it can be
  staged from, writes no version row, and exits 0. The next daily refresh checks again. Any version whose start is today or later gets `activate_on`; one that started earlier
  (a republished timetable) doesn't, and is activated at once.
- **Activation on the eve**: `activate-due` (timer at 23:40 Europe/Berlin) activates the staged
  version whose `activate_on` is tomorrow, so it is active before its first service day starts
  at midnight. It also catches up with one whose day has passed. If several are due, the
  latest start wins, and for the same start the newest import. `refresh` catches up too, but
  never activates a version for tomorrow.
- **Superseded**: activating a version clears `activate_on` on the other staged versions
  starting no later than it (or today), so they are never activated over it afterwards.
- **History and the version of a service day**:
  - `version_activations` has one row per activation: `activated_at`, `effective_from` and
    `deactivated_at`.
  - Service day *d* belongs to the latest activation in effect at *d* 00:00 Europe/Berlin
    (`v_version_periods`, `version_for_service_date(d)`).
  - A staged version activated late, or imported on its first morning, counts from 00:00 on
    its `activate_on`. `effective_from` is never earlier than an earlier activation's.
  - Days before the first activation belong to the earliest version.
  - Join realtime data with
    `ON t.trip_id = x.trip_id AND x.service_date BETWEEN t.first_service_date AND t.last_service_date`.
- **`refresh`**, in order:
  1. download (cache rules above) and import with `--activate` semantics;
  2. back up a new zip;
  3. catch up an overdue staged version;
  4. prune.

  It exits 1 if the download or import failed; the active version stays, and steps 3 and 4
  still run.
- **Pruning** keeps:
  - the active version;
  - the most recently deactivated `retired` one (late updates around a swap);
  - the versions serving yesterday's and today's service days (trips past midnight);
  - every `ready` one;
  - `importing` ones;
  - `failed` ones younger than 7 days (they hold no rows; their row says why).

  The rest is deleted in committed batches of 50,000 rows, then `VACUUM (ANALYZE)`. The log
  line `pruned version … in … s` gives the duration.
- **Backup**: an imported zip (from `import` or `refresh`) is copied to
  `s3://$BTP_RAW_BUCKET/backups/gtfs/<version_id>.zip` with `If-None-Match: *`, as the
  instance role requires. A failure is logged, doesn't fail the import, and is retried on the
  next run until the marker `$BTP_SCHEDULE_CACHE_DIR/backed-up/<version_id>` records success.
  Folders (fixtures) aren't backed up.

## Known limitations

- **Fixture expiry:** the mini fixture's calendar ends on 2027-12-11. After that,
  `BTP_FEED_MODE=fixtures` imports fail validation until the fixtures are re-recorded.
- **Unused ready versions:** a `ready` version that is never activated (imported without
  `--activate`, or a superseded staged one) is never pruned. Delete it by hand.
- **Rollbacks and history:** `v_*_by_service_date` follows the activation history. After
  activating a version by hand and rolling back, the service days in between keep the
  rolled-back version.

## Tests

```sh
uv run pytest services/schedule                       # unit
make up && uv run pytest -m integration services/schedule
```

The integration tests import zips built from the mini fixture, some with shifted calendars
(`tests/conftest.py`). They cover:

- a re-import;
- a second version and switching;
- a rollback that `import --activate` must not undo;
- two concurrent imports of the same zip;
- a failed import;
- staging up to 21 days ahead;
- `activate-due` on the eve and late;
- the version of each service day across a swap in 2030;
- pruning;
- a failed `refresh`;
- the migration of an existing active version.

They restore every version and the activation history as they were.
