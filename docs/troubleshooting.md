# Troubleshooting log

Problems hit while building and running this project, what caused them and how they were
fixed. Newest first. Each entry has the same fields so it can be skimmed:
**Seen** (date, PR or issue, run), **Symptom**, **Cause**, **Fix**, **Status**.

Status is one of: _fixed_, _workaround_ (remove when the upstream fix lands), _open_.

## Feed

### Feed header several minutes old at night

- **Seen:** 2026-11-19 00:32 Berlin time, #6 (feed client), manual live check.
- **Symptom:** the first poll got a 200 whose header timestamp was 448 s old, so the
  "stale feed" warning fired (limit `BTP_FEED_MAX_AGE_S`, 180 s). The next two polls got 304.
- **Cause:** not known yet. VBB may publish less often at night. The rush-hour fixtures had
  header times 20–40 s apart.
- **Fix:** none needed for ingest: stale snapshots are logged and still passed on, as designed.
  Before tuning the limit or alerting on it, check the header age over a full day once ingest
  runs 24/7 (M2).
- **Status:** _open_

### Static GTFS download sends no ETag or Last-Modified

- **Seen:** 2026-11-18, #5 (schedule import), second `make up`.
- **Symptom:** every `make up` downloaded the full ~84 MB static zip again, despite the cache.
- **Cause:** `https://www.vbb.de/vbbgtfs` redirects (307) to `/gtfs`, which answers 200 with no
  `ETag` or `Last-Modified`. A conditional request can't return 304.
- **Fix:** a cached zip younger than `BTP_SCHEDULE_MAX_AGE_HOURS` (default 24) is used without a
  request. Re-importing the same zip is a no-op anyway, because versions are keyed by SHA-256.
- **Status:** _workaround_ (upstream)

### Realtime stop_ids that aren't in the static GTFS

- **Seen:** 2026-11-18, #4 (feed fixtures), rush-hour snapshot 17:00 UTC.
- **Symptom:** the fixture consistency test failed: realtime stop `900062252` isn't in
  the static `stops.txt`.
- **Cause:** VBB sends some stop-time updates with a bare stop number (`900162001`, `290302`)
  instead of the static `stop_id` (`de:11000:900162001::1`). In one full snapshot: 2,742 of
  205,195 updates (1.3%), 290 distinct ids, 1,537 trips (15%). The worst route is an S-Bahn
  route (`19156_109`). Some numbers match the third part of a static id, others match no
  static stop at all.
- **Fix:** match realtime to static by `stop_sequence` within the trip, not by `stop_id`
  (#8). The fixtures keep these ids on purpose; `fixtures/gtfs-static/manifest.json`
  lists them.
- **Status:** _open_ (upstream data)

### VBB feed has been missing some data since 2026-06-04

- **Seen:** 2026-11-18, while confirming feed access before building.
- **Symptom:** the feed page says the feed "has been lacking some data since 2026-06-04 16:00"
  with no restoration estimate. A first check (2026-11-18, 11:37) showed live delays for
  bus, tram and U-Bahn, but only 9% of S-Bahn trips with a non-zero delay.
- **Cause:** upstream data source problems, per VBB. Which parts are missing isn't stated.
  The low S-Bahn share turned out to be a counting artefact, not missing data: the feed lists
  most of the day's S-Bahn trips in advance (~2,945 future trips at 17:25, against 36 for
  U-Bahn), and future trips carry no delay yet. Counting only running trips at rush hour
  (2026-11-18, ~17:25) gave, for S-Bahn: 155 running, 38–39% with a non-zero delay,
  p95 6 min, max 14 min. Live data is present for every mode.
- **Fix:** S-Bahn kept in v1 scope (issue #1). Lesson for every metric: count only trips that
  have actually run, never shares across the whole feed. Coverage per mode will be tracked by
  the data-quality checks (M3), which will show whether anything is still missing.
- **Status:** _workaround_ (what VBB still lacks is unknown; watch coverage per mode and
  the VBB news feed)

### Rate limit reduced to 7 requests per minute

- **Seen:** announced 2026-10-27, effective 2026-11-02.
- **Symptom:** more frequent requests are rejected; generic User-Agents may be blocked.
- **Cause:** excessive traffic on a budget shared by all consumers.
- **Fix:** poll every 30 s, send gzip and If-None-Match, and a User-Agent with contact details.
- **Status:** _fixed_ (by design)

## Database

### Two concurrent imports could mark the active version failed

- **Seen:** 2026-11-18, #5 (schedule import), `/code-review` before the PR. It was never hit in a
  real run.
- **Symptom:** in theory, two imports of the same zip (compose `schedule-import` and a manual
  `btp-schedule import`) both start loading. The second one hits a duplicate key once the first
  commits. Its failure handler then set the version, now active, to `failed`, and every
  `schedule.v_*` view went empty.
- **Cause:** imports weren't serialised, and the failure handler updated the version
  whatever its status.
- **Fix:** imports hold a Postgres advisory lock for their whole run. The failure handler only
  touches a row still in `importing`. An integration test runs two imports at once; it fails
  without the lock (`duplicate key ... stops_pkey`).
- **Status:** _fixed_

### Validation query takes minutes right after a large COPY

- **Seen:** 2026-11-18, #5 (schedule import), first real import.
- **Symptom:** the 5.7M stop_times loaded in about 70 s. Then the check "every
  `stop_times.trip_id` is in trips" ran for over 9 minutes at 100% CPU, until it was cancelled.
- **Cause:** the data was loaded in the same transaction, so the planner had no statistics for
  the new `version_id`. It estimated 1 row and chose a nested-loop anti-join that matched only
  on `version_id`, which is quadratic.
- **Fix:** `ANALYZE` each table right after its `COPY`, inside the transaction. The validation
  now takes seconds. The cancelled run also showed the failure path working: the version was
  marked `failed` and the active version was untouched.
- **Status:** _fixed_

## Tooling

### `make up` hangs on "load metadata for docker.io/library/python:3.12-slim"

- **Seen:** 2026-11-19, #8 (processor), first image build for the new service.
- **Symptom:** `make up` (and `docker build`, `docker pull`) printed nothing for over 10
  minutes; the build stopped at `load metadata`. The registry answered from the host
  (`curl https://registry-1.docker.io/v2/` returned 401 in 0.4 s).
- **Cause:** `docker-credential-desktop get` was waiting for a macOS Keychain prompt
  ("allow access to the Docker credentials?"). Docker reads the Docker Hub login from Keychain
  before every pull; a build started from a terminal or by an AI coding agent just waits until
  the prompt is answered. A Docker Desktop update changes the helper binary, so it asks again.
- **Fix:** restarting Docker Desktop cleared it. To stop the prompts: answer them with
  **Always Allow** (not Allow), or add `docker-credential-desktop` to the Access Control list
  of the "Docker Credentials" items in Keychain Access. Only public images are pulled, so
  `docker logout` (no stored login, nothing to ask for) also works.
- **Status:** _workaround_

### Workspace packages don't import on macOS after `uv sync`

- **Seen:** 2026-11-18, #2 (repo scaffolding), first `make install` on macOS.
- **Symptom:** `uv pip list` shows all `btp-*` packages installed, but
  `uv run python -c "import btp_common"` fails with `ModuleNotFoundError`.
- **Cause:** macOS had set the `hidden` file flag (`ls -lO`) on most of the editable
  `.pth` files in `.venv/lib/python3.12/site-packages/`. Python 3.12.13 skips hidden
  `.pth` files, so the `src/` paths never reach `sys.path`. The repo was under `~/Documents`,
  which iCloud Drive syncs (Desktop & Documents), and iCloud sets that flag.
- **Fix:** keep the repo outside iCloud-synced folders (`~/Documents`, `~/Desktop`), for example
  in `~/Code`. After moving a clone, delete `.venv` and run `make install` again: the
  scripts in `.venv/bin` keep the old path in their shebang, and `uv sync` doesn't rewrite them.
  Also unset a `VIRTUAL_ENV` that still points at the old path. If an old clone has to stay in
  iCloud, `chflags -R nohidden .venv` after each sync clears the flag.
- **Status:** _fixed_ (clone moved out of iCloud Drive, 2026-11-18); the `chflags` step was removed
  from `make install`
