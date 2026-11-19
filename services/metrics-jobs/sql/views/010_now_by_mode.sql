-- Live indicator, not a reliability metric: trips running now and their current delay, per
-- mode, for Berlin trips of the active schedule. On-time rates, coverage thresholds and the
-- other metric definitions wait for ADR-0005 (M3); don't add them here.
--
-- Reference time ("now"): the session setting btp.as_of if set, else now() (ADR-0018). The
-- API sets it in fixtures mode to the recording time of the replayed snapshots.
--
-- Running trip: in trip_state and not removed, not CANCELED, in Berlin, and its earliest
--   predicted time <= now <= its latest predicted time (predicted time = departure_time,
--   else arrival_time).
-- Delays are in seconds, rounded to 0.1 s; percentiles are percentile_cont.
-- Current delay: at the first stop (by stop_sequence) whose predicted time is >= now;
--   departure_delay, else arrival_delay. NULL if that stop has neither.
-- cancelled_trips_today: CANCELED Berlin trips of today's service day (Europe/Berlin), also
--   those already removed from the feed.
--
-- Only service days today and yesterday are read: a trip running now started on one of
-- them. Trips not in the active schedule (e.g. ADDED trips) have no mode and are left out.
--
-- Plan: start from the few trips still in the feed (removed_at IS NULL; ingest tombstones
-- finished trips) and read each one's stops through the stop_predictions primary key, rather
-- than aggregating every prediction of the day. See services/metrics-jobs/README.md.
CREATE OR REPLACE VIEW marts.v_now_by_mode AS
WITH ref AS MATERIALIZED (
    SELECT
        r.at,
        (r.at AT TIME ZONE 'Europe/Berlin')::date AS today
    FROM (
        SELECT coalesce(nullif(current_setting('btp.as_of', true), '')::timestamptz, now()) AS at
    ) r
),
-- Trips that may be running (still in the feed, not cancelled) and today's cancellations.
candidates AS (
    SELECT
        ts.trip_id,
        ts.service_date,
        ts.schedule_relationship = 'CANCELED' AS cancelled
    FROM events.trip_state ts
    CROSS JOIN ref
    WHERE ts.service_date BETWEEN ref.today - 1 AND ref.today
      AND (
          (ts.removed_at IS NULL AND ts.schedule_relationship <> 'CANCELED')
          OR (ts.schedule_relationship = 'CANCELED' AND ts.service_date = ref.today)
      )
),
berlin AS MATERIALIZED (
    SELECT c.trip_id, c.service_date, c.cancelled, r.mode
    FROM candidates c
    JOIN schedule.v_trips t ON t.trip_id = c.trip_id
    JOIN schedule.v_routes r ON r.route_id = t.route_id
    WHERE t.in_berlin
),
running AS (
    SELECT b.mode, s.delay_s
    FROM berlin b
    CROSS JOIN ref
    CROSS JOIN LATERAL (
        SELECT
            min(x.predicted) AS first_ts,
            max(x.predicted) AS last_ts,
            (array_agg(x.delay ORDER BY p.stop_sequence) FILTER (WHERE x.predicted >= ref.at))[1]
                AS delay_s
        FROM events.stop_predictions p
        CROSS JOIN LATERAL (
            SELECT
                coalesce(p.departure_time, p.arrival_time) AS predicted,
                coalesce(p.departure_delay, p.arrival_delay) AS delay
        ) x
        WHERE p.trip_id = b.trip_id
          AND p.service_date = b.service_date
    ) s
    WHERE NOT b.cancelled
      AND s.first_ts <= ref.at
      AND ref.at <= s.last_ts
),
by_mode AS (
    SELECT
        mode,
        count(*) AS running_trips,
        count(*) FILTER (WHERE delay_s <> 0) AS trips_with_nonzero_delay,
        round(avg(delay_s), 1)::double precision AS avg_delay_s,
        round(percentile_cont(0.5) WITHIN GROUP (ORDER BY delay_s)::numeric, 1)::double precision
            AS p50_delay_s,
        round(percentile_cont(0.95) WITHIN GROUP (ORDER BY delay_s)::numeric, 1)::double precision
            AS p95_delay_s
    FROM running
    GROUP BY mode
),
cancelled AS (
    SELECT mode, count(*) AS cancelled_trips_today
    FROM berlin
    WHERE cancelled
    GROUP BY mode
)
SELECT
    mode,
    coalesce(m.running_trips, 0)            AS running_trips,
    coalesce(m.trips_with_nonzero_delay, 0) AS trips_with_nonzero_delay,
    m.avg_delay_s,
    m.p50_delay_s,
    m.p95_delay_s,
    coalesce(c.cancelled_trips_today, 0)    AS cancelled_trips_today,
    f.latest_feed_ts,
    f.computed_at
FROM by_mode m
FULL JOIN cancelled c USING (mode)
CROSS JOIN marts.v_feed_status f
ORDER BY mode;
