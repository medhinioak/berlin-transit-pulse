-- Freshness of the realtime data: the newest feed time the processor has applied.
-- metrics-api reads this for /health; btp_api can't read events directly.
CREATE OR REPLACE VIEW marts.v_feed_status AS
SELECT
    max(feed_ts) AS latest_feed_ts,
    now()        AS computed_at
FROM events.trip_state;
