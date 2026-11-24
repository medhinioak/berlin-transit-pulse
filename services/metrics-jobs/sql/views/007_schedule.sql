-- The active schedule version and its trip_ids, for metrics-api's fixtures-mode check (#75):
-- are the replayed trips in the active schedule? btp_api reads marts only, never schedule.
-- No active version: v_schedule_status has no row and v_schedule_trips is empty.
CREATE OR REPLACE VIEW marts.v_schedule_status AS
SELECT version_id, feed_start_date, feed_end_date, activated_at
FROM schedule.v_active_version;

CREATE OR REPLACE VIEW marts.v_schedule_trips AS
SELECT trip_id
FROM schedule.v_trips;
