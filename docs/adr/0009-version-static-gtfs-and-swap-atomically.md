# ADR-0009: Version the static GTFS and swap versions atomically

- Status: Accepted
- Date: 2026-11-18
- Decider: Maintainer (@medhinioak)
- Review: decisions taken in issue #5; implementation reviewed with Claude Code `/code-review`,
  2026-11-18

## Context

Realtime updates carry only trip and stop IDs. Line names, modes, stop names and the Berlin flag
(ADR-0017) come from VBB's static GTFS. VBB republishes it every few weeks, and at the annual
timetable change (next: 2026-12-13) the trip IDs change too. The real zip is ~84 MB, with
5.7M stop_times, 257k trips and 42k stops. The release server is a t4g.small with 2 GB RAM, and
Postgres is limited to 512 MB.

The schedule must never be half-imported or briefly empty when consumers read it. A bad zip
must not replace a good schedule, and it must be possible to go back to the previous one.

## Decision

Import every static GTFS zip into schema `schedule` as a separate **version**, and let consumers
read only views that show the **one active version**.

- **Version id:** the first 12 hex characters of the zip's SHA-256. Importing the same zip
  again is a no-op.
- **Tables:** every table carries `version_id` in its primary key. `schedule.versions` records
  the source, the feed date range, row counts, status and failure reason.
- **Status:** `importing` → `ready` → `active` → `retired`, or `failed`. A partial unique index
  allows only one `active` row.
- **Loading:** files stream in with `COPY` (psycopg 3). stop_times is never held in memory. The
  data and the `ready` status commit in one transaction.
- **Validation before `ready`:**
  - the required files are present;
  - routes, trips, stops and stop_times are not empty;
  - every `stop_times.trip_id` exists in trips;
  - the feed date range covers today.
- **Activation:** one transaction retires the active version and activates the new one.
  Consumers read `schedule.v_*` views, which join to `versions` on `status = 'active'`, so they
  switch atomically. A retired version can be activated again to roll back.
- **Concurrency:** imports hold a Postgres advisory lock, so two at once (e.g. compose and a
  manual run) are serialised.
- **Times:** GTFS times are kept as text plus integer seconds since the start of the service
  day. They are never parsed as clock times, because they can exceed 24:00.

## Alternatives considered

- **Truncate and reload one set of tables:** consumers would see an empty or partial schedule
  during the load, and there would be no rollback.
- **One schema per version, swapped by renaming or `search_path`:** atomic too, but grants,
  default privileges and the views would need to be rebuilt for every version. Consumers would
  also need to know a schema name that changes.
- **Partitioning by `version_id`:** dropping an old version would be cheap, but it adds DDL for
  every import. It's worth revisiting if pruning (below) turns out slow.
- **Loading in Python (pandas or `executemany`):** stop_times would not fit the memory budget.
  `COPY` peaks at ~65 MB of RSS for the real feed.

## Consequences

- **Easier:**
  - Consumers never see a partial schedule.
  - A failed import is recorded with its reason and leaves the active version untouched.
  - Rollback is `btp-schedule activate <id>`, and `make up` doesn't undo it, because
    `import --activate` never re-activates a retired version.
  - Realtime data can still be matched to the previous version during a swap.
- **Harder:**
  - Every version costs ~1 GB of Postgres disk (stop_times and its primary key). Retired
    versions must be pruned before the weekly refresh (#24) runs for long. Pruning is not built
    yet.
  - Consumers must use the views, never the tables. Joining by `trip_id` across versions is
    wrong after a timetable change.
  - The "covers today" check rejects a zip published before its start date. A timetable that
    starts on 2026-12-13 can't be staged as `ready` early. #24 has to decide whether staging
    allows a start date in the near future. The mini fixture also stops validating after its
    calendar ends on 2027-12-11.
- **Cost:** no AWS cost of its own. It adds ~1 GB of disk per kept version on the release
  server.
- **Revisit if:** pruning is slow or disk is short (partition by version), or a consumer needs a
  schedule other than the active one.
