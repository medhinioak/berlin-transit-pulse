-- Latest known state per trip and per stop, from the trip-updates topic (schemas/trip-updates.md).
-- Idempotent: the processor applies this on every start as btp_processor, so the tables are
-- owned by that role (see deploy/compose/README.md). Timestamps are UTC (timestamptz).
--
-- feed_ts is the btp-feed-ts header of the message a row was last written from. A message
-- is applied to a row only if its feed_ts is >= the stored one, so replays, duplicates and
-- late deliveries can't move a row back in time.

CREATE TABLE IF NOT EXISTS events.trip_state (
    trip_id               text        NOT NULL,
    service_date          date        NOT NULL,  -- TripDescriptor.start_date
    route_id              text,
    start_time            text,                  -- GTFS time as sent, may exceed 24:00:00
    schedule_relationship text        NOT NULL,  -- SCHEDULED, CANCELED, ADDED, ...
    feed_ts               timestamptz NOT NULL,
    removed_at            timestamptz,           -- feed_ts of the tombstone; NULL while in the feed
    updated_at            timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (trip_id, service_date)
);

CREATE TABLE IF NOT EXISTS events.stop_predictions (
    trip_id                    text        NOT NULL,
    service_date               date        NOT NULL,
    stop_sequence              integer     NOT NULL,
    stop_id                    text,
    arrival_time               timestamptz,
    arrival_delay              integer,     -- seconds
    departure_time             timestamptz,
    departure_delay            integer,     -- seconds
    stop_schedule_relationship text        NOT NULL,  -- SCHEDULED, SKIPPED, NO_DATA, ...
    feed_ts                    timestamptz NOT NULL,
    updated_at                 timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (trip_id, service_date, stop_sequence)
);

CREATE INDEX IF NOT EXISTS stop_predictions_service_date_departure
    ON events.stop_predictions (service_date, departure_time);
