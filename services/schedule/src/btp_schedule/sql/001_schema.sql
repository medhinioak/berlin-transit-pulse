-- Versioned static GTFS. Every table carries version_id; consumers read only the v_* views,
-- which show the one active version. Idempotent: the CLI applies this on every start as
-- btp_schedule, so the tables are owned by that role (see deploy/compose/README.md).

CREATE TABLE IF NOT EXISTS schedule.versions (
    version_id      text PRIMARY KEY,           -- first 12 hex characters of sha256
    sha256          text NOT NULL,
    source_url      text NOT NULL,
    downloaded_at   timestamptz,
    feed_start_date date,
    feed_end_date   date,
    row_counts      jsonb,
    status          text NOT NULL
        CHECK (status IN ('importing', 'ready', 'active', 'retired', 'failed')),
    failure_reason  text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    activated_at    timestamptz
);

-- At most one active version.
CREATE UNIQUE INDEX IF NOT EXISTS versions_one_active
    ON schedule.versions (status) WHERE status = 'active';

CREATE TABLE IF NOT EXISTS schedule.agency (
    version_id      text NOT NULL,
    agency_id       text NOT NULL,
    agency_name     text,
    agency_url      text,
    agency_timezone text,
    agency_lang     text,
    agency_phone    text,
    PRIMARY KEY (version_id, agency_id)
);

CREATE TABLE IF NOT EXISTS schedule.routes (
    version_id       text NOT NULL,
    route_id         text NOT NULL,
    agency_id        text,
    route_short_name text,
    route_long_name  text,
    route_type       integer NOT NULL,
    route_color      text,
    route_text_color text,
    route_desc       text,
    mode             text NOT NULL,             -- btp_schedule.modes.Mode
    in_berlin        boolean NOT NULL,          -- any of its trips is in Berlin
    PRIMARY KEY (version_id, route_id)
);

CREATE TABLE IF NOT EXISTS schedule.trips (
    version_id            text NOT NULL,
    route_id              text NOT NULL,
    service_id            text NOT NULL,
    trip_id               text NOT NULL,
    trip_headsign         text,
    trip_short_name       text,
    direction_id          smallint,
    block_id              text,
    shape_id              text,
    wheelchair_accessible smallint,
    bikes_allowed         smallint,
    in_berlin             boolean NOT NULL,     -- any of its stops is in Berlin (de:11000:)
    PRIMARY KEY (version_id, trip_id)
);

CREATE INDEX IF NOT EXISTS trips_version_route ON schedule.trips (version_id, route_id);

CREATE TABLE IF NOT EXISTS schedule.stops (
    version_id          text NOT NULL,
    stop_id             text NOT NULL,
    stop_code           text,
    stop_name           text,
    stop_desc           text,
    stop_lat            double precision,
    stop_lon            double precision,
    location_type       smallint,
    parent_station      text,
    wheelchair_boarding smallint,
    platform_code       text,
    zone_id             text,
    level_id            text,
    PRIMARY KEY (version_id, stop_id)
);

-- Times stay as the original GTFS text (may exceed 24:00); *_secs are seconds since the start
-- of the service day. Turning them into timestamps needs the service date ("noon minus 12 h").
CREATE TABLE IF NOT EXISTS schedule.stop_times (
    version_id     text NOT NULL,
    trip_id        text NOT NULL,
    stop_id        text NOT NULL,
    stop_sequence  integer NOT NULL,
    arrival_time   text,
    departure_time text,
    pickup_type    smallint,
    drop_off_type  smallint,
    stop_headsign  text,
    arrival_secs   integer,
    departure_secs integer,
    PRIMARY KEY (version_id, trip_id, stop_sequence)
);

CREATE TABLE IF NOT EXISTS schedule.calendar (
    version_id text NOT NULL,
    service_id text NOT NULL,
    monday     smallint NOT NULL,
    tuesday    smallint NOT NULL,
    wednesday  smallint NOT NULL,
    thursday   smallint NOT NULL,
    friday     smallint NOT NULL,
    saturday   smallint NOT NULL,
    sunday     smallint NOT NULL,
    start_date date NOT NULL,
    end_date   date NOT NULL,
    PRIMARY KEY (version_id, service_id)
);

CREATE TABLE IF NOT EXISTS schedule.calendar_dates (
    version_id     text NOT NULL,
    service_id     text NOT NULL,
    date           date NOT NULL,
    exception_type smallint NOT NULL,
    PRIMARY KEY (version_id, service_id, date)
);

CREATE TABLE IF NOT EXISTS schedule.feed_info (
    version_id          text PRIMARY KEY,
    feed_publisher_name text,
    feed_publisher_url  text,
    feed_lang           text,
    feed_start_date     date,
    feed_end_date       date,
    feed_version        text
);

-- One view per table: only the active version. Switching versions is one UPDATE on versions.
CREATE OR REPLACE VIEW schedule.v_agency AS
    SELECT t.* FROM schedule.agency t
    JOIN schedule.versions v USING (version_id) WHERE v.status = 'active';
CREATE OR REPLACE VIEW schedule.v_routes AS
    SELECT t.* FROM schedule.routes t
    JOIN schedule.versions v USING (version_id) WHERE v.status = 'active';
CREATE OR REPLACE VIEW schedule.v_trips AS
    SELECT t.* FROM schedule.trips t
    JOIN schedule.versions v USING (version_id) WHERE v.status = 'active';
CREATE OR REPLACE VIEW schedule.v_stops AS
    SELECT t.* FROM schedule.stops t
    JOIN schedule.versions v USING (version_id) WHERE v.status = 'active';
CREATE OR REPLACE VIEW schedule.v_stop_times AS
    SELECT t.* FROM schedule.stop_times t
    JOIN schedule.versions v USING (version_id) WHERE v.status = 'active';
CREATE OR REPLACE VIEW schedule.v_calendar AS
    SELECT t.* FROM schedule.calendar t
    JOIN schedule.versions v USING (version_id) WHERE v.status = 'active';
CREATE OR REPLACE VIEW schedule.v_calendar_dates AS
    SELECT t.* FROM schedule.calendar_dates t
    JOIN schedule.versions v USING (version_id) WHERE v.status = 'active';
CREATE OR REPLACE VIEW schedule.v_feed_info AS
    SELECT t.* FROM schedule.feed_info t
    JOIN schedule.versions v USING (version_id) WHERE v.status = 'active';
