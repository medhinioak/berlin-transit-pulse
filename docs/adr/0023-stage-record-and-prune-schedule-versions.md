# ADR-0023: Stage, record and prune schedule versions

- Status: Accepted
- Date: 2026-11-24
- Decider: Maintainer (@medhinioak)
- Review: decisions taken in issue #24; late activation and days before the first activation
  decided with Claude Code while planning #24; superseded staged versions, ordered
  activations, versions in use and the early refusal added after `/code-review` on PR #93,
  2026-11-24
- Extends [ADR-0009](0009-version-static-gtfs-and-swap-atomically.md)

## Context

ADR-0009 imports each static GTFS zip as a version and swaps the active one atomically. Three of
its consequences block the annual timetable change on 2026-12-13, when every trip ID changes:

- A zip published before its start date fails the "covers today" check, so it can't be imported
  ahead of time.
- Only the active version is known. A trip of 12 December still running at 00:30 on the 13th
  keeps its old trip ID, but after the swap only the new version can be looked up.
- Versions are never deleted. At ~1 GB each, a daily refresh fills the 30 GB disk.

## Decision

- **Staging:**
  - A feed whose start date is up to 21 days after today validates as before and becomes
    `ready` with `activate_on = feed_start_date`.
  - A feed starting further ahead is refused before loading: the start date is read from
    feed_info, calendar and calendar_dates only. No version row is written, and the command
    exits 0 with a warning. Each daily refresh checks again until the window opens, without
    loading 5.7M stop_times only to roll them back.
  - A version whose start is today or later gets `activate_on`. One that started in the past
    (a republished timetable) doesn't.
  - `import --activate` and `refresh` activate a version immediately unless its `activate_on`
    is after today.
- **Activation on the eve:** `btp-schedule activate-due` runs at 23:40 Europe/Berlin and
  activates the staged version whose `activate_on` is tomorrow, or one whose day has passed
  (catch-up). If several are due, the latest start wins, and of those with the same start
  (a corrected republish) the newest import. The morning refresh also catches up, but never
  activates a version for tomorrow.
- **Superseded staged versions:** an activation clears `activate_on` on every other staged
  version starting on or before the activated one's start (or today, for a version without
  `activate_on`). This stops a passed-over or overdue staged version from being activated
  later over the newer timetable. Versions starting later stay staged.
- **Activation history:** every activation is a row in `schedule.version_activations`
  (`activated_at`, `effective_from`, `deactivated_at`), written in the same transaction as the
  status change. The table has no foreign key, so the history survives pruning.
  - `effective_from` is `activated_at`.
  - Exception: the first activation of a version with `activate_on` uses
    `min(activated_at, activate_on 00:00 Europe/Berlin)`. So a staged version activated late
    (catch-up, or imported by hand on its first morning) still applies to its first day.
  - `effective_from` is never earlier than the latest one so far. Activations stay in order,
    so a backdated one can't hand later days back to a version activated before it.
- **Version per service day:**
  - Service day *d* belongs to the latest activation (by `effective_from`) in effect at *d*
    00:00 Europe/Berlin.
  - Days before the first activation belong to the earliest activation. This covers fixtures
    replayed from before the stack started, and history from before the server's first import.
  - `schedule.v_version_periods` gives each activation its `first_service_date` and
    `last_service_date`, open-ended with `-infinity` and `infinity`.
  - `schedule.version_for_service_date(d)` returns the version for one day. The
    `v_trips_by_service_date`, `v_routes_by_service_date` and `v_stop_times_by_service_date`
    views join the tables to those periods.
  - The `v_*` views of the active version stay for "now" queries.
- **Pruning:**
  - Each refresh keeps:
    - the active version;
    - the most recently deactivated `retired` one (late updates around a swap);
    - the versions serving yesterday's and today's service days, since trips run past
      midnight;
    - every `ready` one and `importing` ones;
    - `failed` ones younger than 7 days. They hold no rows; their `versions` row says why
      they failed.
  - It deletes the rest by `version_id`, in batches of 50,000 rows per statement, each batch
    committed. Then it runs `VACUUM (ANALYZE)`, not `FULL`: the next import reuses the space.
- **Backup:** each imported zip is copied to `s3://<data bucket>/backups/gtfs/<version_id>.zip`
  with `If-None-Match: *`, as the instance role requires. A failed backup is logged and doesn't
  fail the import. It is retried on the next import or refresh of the same zip, until a
  marker in the cache directory records success.

## Alternatives considered

- **Use only `activated_at` for the service-day mapping (as #24 first specified):** a late
  activation would leave the first day of the new timetable on the old version. Its trip IDs
  don't match, and that day's live data would be lost.
- **NULL for days before the first activation:** fixtures (recorded 2026-11-18, imported when
  the stack starts) would find no version.
- **A row per calendar day (`generate_series`):** it needs bounds. Days outside them silently get
  no version (tests use 2030).
- **Partitioning `stop_times` by `version_id`:** dropping a partition is instant, but every import
  needs DDL. Revisit if a prune of one version takes more than 15 minutes on the server.
- **Activate a staged version at midnight exactly:** the swap would race the first trips of the
  day, and a timer at 00:00 has no slack. 23:40 is after the last evening refresh and before the
  new service day.

## Consequences

- **Easier:**
  - The 13 December timetable can be imported in advance and becomes active by itself on the
    evening of the 12th.
  - Consumers can join each `(trip_id, service_date)` to the right version (#22, #29).
  - Disk use stays at two or three versions.
- **Harder:**
  - Two places say "which version": `versions.status` for now, `version_activations` for a
    service day. `activate()` writes both in one transaction, and nothing else may write
    either.
  - `v_now_by_mode` follows the history. If a version is activated by hand and then rolled
    back, the service days in between keep the rolled-back version.
  - A `ready` version that is never activated is never pruned. Delete it by hand.
- **Cost:** about 84 MB in S3 per imported zip (a few cents per month); no other cost.
- **Revisit if:** VBB publishes more than 21 days ahead, or pruning is slow (partition by
  version).
