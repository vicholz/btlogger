CREATE EXTENSION IF NOT EXISTS timescaledb;

CREATE TABLE devices (
    id                  BIGSERIAL PRIMARY KEY,
    address             TEXT NOT NULL,
    address_type        TEXT NOT NULL,
    first_seen          TIMESTAMPTZ NOT NULL,
    last_seen           TIMESTAMPTZ NOT NULL,
    last_rssi           SMALLINT,
    name                TEXT,
    manufacturer_id     INTEGER,
    manufacturer_name   TEXT,
    device_type         TEXT,
    appearance          INTEGER,
    appearance_name     TEXT,
    identity_hint       TEXT,
    service_uuids       TEXT[] NOT NULL DEFAULT '{}',
    sighting_count      BIGINT NOT NULL DEFAULT 0,
    packet_count        BIGINT NOT NULL DEFAULT 0,
    visit_count         BIGINT NOT NULL DEFAULT 0,
    total_dwell_seconds BIGINT NOT NULL DEFAULT 0,
    last_visit_start    TIMESTAMPTZ,
    last_visit_end      TIMESTAMPTZ,
    last_payload        BYTEA,
    last_parsed         JSONB,
    notes               TEXT,
    UNIQUE (address, address_type)
);

CREATE INDEX devices_last_seen_idx ON devices (last_seen DESC);
CREATE INDEX devices_type_idx ON devices (device_type);
CREATE INDEX devices_mfg_idx ON devices (manufacturer_name);
CREATE INDEX devices_identity_idx ON devices (identity_hint);
CREATE INDEX devices_name_idx ON devices (name);

CREATE TABLE sightings (
    time            TIMESTAMPTZ NOT NULL,
    device_id       BIGINT NOT NULL,
    rssi            SMALLINT,
    adv_type        TEXT,
    payload         BYTEA,
    parsed          JSONB,
    device_type     TEXT,
    name            TEXT
);

SELECT create_hypertable('sightings', 'time', if_not_exists => TRUE);

CREATE INDEX sightings_device_time_idx ON sightings (device_id, time DESC);
CREATE INDEX sightings_type_time_idx ON sightings (device_type, time DESC);
CREATE INDEX sightings_parsed_gin ON sightings USING GIN (parsed);

ALTER TABLE sightings SET (
    timescaledb.compress,
    timescaledb.compress_orderby = 'time DESC',
    timescaledb.compress_segmentby = 'device_id'
);

SELECT add_compression_policy('sightings', INTERVAL '7 days', if_not_exists => TRUE);
SELECT add_retention_policy('sightings', INTERVAL '90 days', if_not_exists => TRUE);

CREATE MATERIALIZED VIEW IF NOT EXISTS sightings_hourly
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 hour', time) AS bucket,
    device_id,
    COUNT(*) AS sightings,
    AVG(rssi)::SMALLINT AS avg_rssi,
    MIN(rssi) AS min_rssi,
    MAX(rssi) AS max_rssi
FROM sightings
GROUP BY bucket, device_id
WITH NO DATA;

SELECT add_continuous_aggregate_policy(
    'sightings_hourly',
    start_offset => INTERVAL '3 days',
    end_offset   => INTERVAL '1 hour',
    schedule_interval => INTERVAL '15 minutes',
    if_not_exists => TRUE
);

CREATE TABLE visits (
    id                  BIGSERIAL,
    device_id           BIGINT NOT NULL,
    start_time          TIMESTAMPTZ NOT NULL,
    end_time            TIMESTAMPTZ NOT NULL,
    duration_seconds    INTEGER NOT NULL,
    sightings           INTEGER NOT NULL DEFAULT 1,
    min_rssi            SMALLINT,
    max_rssi            SMALLINT,
    avg_rssi            SMALLINT,
    PRIMARY KEY (id, start_time)
);

SELECT create_hypertable('visits', 'start_time', if_not_exists => TRUE);

CREATE INDEX visits_device_start_idx ON visits (device_id, start_time DESC);
CREATE INDEX visits_duration_idx ON visits (duration_seconds DESC);

CREATE TABLE ingest_meta (
    key     TEXT PRIMARY KEY,
    value   TEXT NOT NULL
);
