-- Staged versions, activation history and the version of each service day (#24, ADR-0023).
-- Idempotent, applied after 001_schema.sql on every start.

-- The day a staged version starts: its feed_start_date when that was today or later at import.
ALTER TABLE schedule.versions ADD COLUMN IF NOT EXISTS activate_on date;

-- One row per activation. effective_from is when the version starts to apply to service days:
-- activated_at, or 00:00 Europe/Berlin of activate_on if that is earlier (a staged version
-- activated late, or on its first day). No foreign key: the history outlives pruning.
CREATE TABLE IF NOT EXISTS schedule.version_activations (
    activation_id  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    version_id     text NOT NULL,
    activated_at   timestamptz NOT NULL,
    effective_from timestamptz NOT NULL,
    deactivated_at timestamptz
);

-- At most one open activation: the active version's.
CREATE UNIQUE INDEX IF NOT EXISTS version_activations_one_open
    ON schedule.version_activations ((true)) WHERE deactivated_at IS NULL;

-- Migration: a version active before this table existed gets its activation row.
INSERT INTO schedule.version_activations (version_id, activated_at, effective_from)
SELECT version_id, coalesce(activated_at, created_at), coalesce(activated_at, created_at)
FROM schedule.versions
WHERE status = 'active'
  AND NOT EXISTS (SELECT 1 FROM schedule.version_activations WHERE deactivated_at IS NULL);

-- The service days each activation applies to. Day d belongs to the latest activation (by
-- effective_from) that took effect by d 00:00 Europe/Berlin: from the first midnight at or
-- after effective_from to the day before the next activation's first day. The first
-- activation also covers every earlier day (e.g. fixtures replayed from before the stack
-- started), the open one every later day. An activation superseded before its first midnight
-- covers no day and has no row.
CREATE OR REPLACE VIEW schedule.v_version_periods AS
WITH starts AS (
    SELECT
        activation_id,
        version_id,
        effective_from,
        ((effective_from AT TIME ZONE 'Europe/Berlin') - interval '1 microsecond')::date + 1
            AS first_day
    FROM schedule.version_activations
),
periods AS (
    SELECT
        activation_id,
        version_id,
        CASE WHEN row_number() OVER w = 1 THEN '-infinity'::date ELSE first_day END
            AS first_service_date,
        coalesce(lead(first_day) OVER w - 1, 'infinity'::date) AS last_service_date
    FROM starts
    WINDOW w AS (ORDER BY effective_from, activation_id)
)
SELECT activation_id, version_id, first_service_date, last_service_date
FROM periods
WHERE first_service_date <= last_service_date;

-- The version that applies to service day d (v_version_periods); NULL if none was activated.
CREATE OR REPLACE FUNCTION schedule.version_for_service_date(d date) RETURNS text
LANGUAGE sql STABLE AS $$
    SELECT version_id FROM schedule.v_version_periods
    WHERE d BETWEEN first_service_date AND last_service_date
$$;

-- Each version's rows with the service days they apply to. Join on trip_id (or route_id) and
-- service_date BETWEEN first_service_date AND last_service_date: across a timetable change, a
-- trip of the old service day keeps its old version.
CREATE OR REPLACE VIEW schedule.v_trips_by_service_date AS
    SELECT p.first_service_date, p.last_service_date, t.*
    FROM schedule.v_version_periods p JOIN schedule.trips t USING (version_id);
CREATE OR REPLACE VIEW schedule.v_routes_by_service_date AS
    SELECT p.first_service_date, p.last_service_date, r.*
    FROM schedule.v_version_periods p JOIN schedule.routes r USING (version_id);
CREATE OR REPLACE VIEW schedule.v_stop_times_by_service_date AS
    SELECT p.first_service_date, p.last_service_date, st.*
    FROM schedule.v_version_periods p JOIN schedule.stop_times st USING (version_id);
