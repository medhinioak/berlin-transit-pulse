# Troubleshooting log

Problems hit while building and running this project, what caused them and how they were
fixed. Newest first. Each entry has the same fields so it can be skimmed:
**Seen** (date, PR or issue, run), **Symptom**, **Cause**, **Fix**, **Status**.

Status is one of: _fixed_, _workaround_ (remove when the upstream fix lands), _open_.

## Feed

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

## Tooling

### Workspace packages don't import on macOS after `uv sync`

- **Seen:** 2026-11-18, #2 (repo scaffolding), first `make install` on the Mac.
- **Symptom:** `uv pip list` shows all `btp-*` packages installed, but
  `uv run python -c "import btp_common"` fails with `ModuleNotFoundError`.
- **Cause:** macOS had set the `hidden` file flag (`ls -lO`) on most of the editable
  `.pth` files in `.venv/lib/python3.12/site-packages/`. Python 3.12.13 skips hidden
  `.pth` files, so the `src/` paths never reach `sys.path`. The repo was under `~/Documents`,
  which iCloud Drive syncs (Desktop & Documents), and iCloud sets that flag.
- **Fix:** keep the repo outside iCloud-synced folders (`~/Documents`, `~/Desktop`). It now
  lives in `~/Code`. After moving a clone, delete `.venv` and run `make install` again: the
  scripts in `.venv/bin` keep the old path in their shebang, and `uv sync` doesn't rewrite them.
  Also unset a `VIRTUAL_ENV` that still points at the old path. If an old clone has to stay in
  iCloud, `chflags -R nohidden .venv` after each sync clears the flag.
- **Status:** _fixed_ (repo moved to `~/Code`, 2026-11-18); the `chflags` step was removed
  from `make install`
