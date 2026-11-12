BEGIN;

CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS core;
CREATE SCHEMA IF NOT EXISTS analysis;
CREATE SCHEMA IF NOT EXISTS app;

CREATE TABLE IF NOT EXISTS raw.import_batch (
    batch_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_id text NOT NULL,
    sha256 text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    original_csv bytea NOT NULL,
    received_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (source_id, sha256)
);

CREATE TABLE IF NOT EXISTS raw.observation_import (
    batch_id bigint NOT NULL REFERENCES raw.import_batch (batch_id),
    line_no integer NOT NULL,
    cells jsonb NOT NULL,
    normalized jsonb,
    status text NOT NULL CHECK (
        status IN ('pending', 'invalid', 'unmapped', 'imported', 'duplicate', 'conflict')
    ),
    reason text,
    PRIMARY KEY (batch_id, line_no)
);

CREATE TABLE IF NOT EXISTS core.device (
    device_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_id text NOT NULL,
    machine_no text NOT NULL,
    point_no integer NOT NULL CHECK (point_no > 0),
    is_demo boolean NOT NULL DEFAULT false,
    UNIQUE (source_id, machine_no)
);

CREATE TABLE IF NOT EXISTS core.observation (
    device_id bigint NOT NULL REFERENCES core.device (device_id),
    observed_at timestamptz NOT NULL,
    observation_day date NOT NULL,
    point_no integer NOT NULL,
    is_demo boolean NOT NULL,
    measurements jsonb NOT NULL,
    batch_id bigint NOT NULL,
    line_no integer NOT NULL,
    PRIMARY KEY (device_id, observed_at),
    FOREIGN KEY (batch_id, line_no)
        REFERENCES raw.observation_import (batch_id, line_no)
);

CREATE INDEX IF NOT EXISTS observation_daily_idx
    ON core.observation (device_id, observation_day);
CREATE INDEX IF NOT EXISTS observation_batch_idx
    ON core.observation (batch_id);

CREATE TABLE IF NOT EXISTS analysis.observation_stats (
    device_id bigint NOT NULL REFERENCES core.device (device_id),
    point_no integer NOT NULL,
    is_demo boolean NOT NULL,
    observation_day date NOT NULL,
    metric text NOT NULL,
    reported_count bigint NOT NULL,
    valid_count bigint NOT NULL,
    missing_value_count bigint NOT NULL,
    mean_value numeric,
    median_value double precision,
    sum_value numeric,
    calculated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (device_id, observation_day, point_no, is_demo, metric)
);

CREATE OR REPLACE VIEW app.point_daily_stats AS
SELECT d.source_id, d.machine_no, s.point_no, s.is_demo, s.observation_day, s.metric,
       s.reported_count, s.valid_count, s.missing_value_count,
       s.mean_value, s.median_value, s.sum_value, s.calculated_at
FROM analysis.observation_stats AS s
JOIN core.device AS d USING (device_id);

CREATE TABLE IF NOT EXISTS analysis.hourly_stats (
    device_id bigint NOT NULL REFERENCES core.device (device_id),
    hour_local timestamp NOT NULL,
    point_no integer NOT NULL,
    is_demo boolean NOT NULL,
    max_value numeric,
    valid_count bigint NOT NULL,
    reported_count bigint NOT NULL,
    PRIMARY KEY (device_id, hour_local, point_no, is_demo)
);

ALTER TABLE analysis.hourly_stats
    ADD COLUMN IF NOT EXISTS max_observed_at timestamptz;

CREATE OR REPLACE VIEW app.point_hourly_stats AS
SELECT d.source_id, d.machine_no, s.point_no, s.is_demo, s.hour_local,
       s.max_value, s.valid_count, s.reported_count, s.max_observed_at
FROM analysis.hourly_stats AS s
JOIN core.device AS d USING (device_id);

CREATE OR REPLACE VIEW app.demo_alerts AS
SELECT point_no, hour_local, max_value AS reading, 25 AS threshold,
       max_observed_at AT TIME ZONE 'Asia/Shanghai' AS observed_local
FROM app.point_hourly_stats
WHERE source_id = 'demo' AND is_demo AND point_no BETWEEN 1 AND 7
  AND machine_no = 'P' || lpad(point_no::text, 2, '0') || '-DEMO'
  AND max_value > 25 AND max_observed_at IS NOT NULL;

CREATE OR REPLACE FUNCTION analysis.refresh_hourly_stats(target_batch bigint)
RETURNS void
LANGUAGE sql
SET search_path = pg_catalog
AS $hourly$
    WITH affected AS (
        SELECT DISTINCT device_id, observation_day, point_no, is_demo
        FROM core.observation
        WHERE target_batch IS NULL OR batch_id = target_batch
    ), samples AS (
        SELECT o.device_id, o.point_no, o.is_demo,
               date_trunc('hour', o.observed_at AT TIME ZONE 'Asia/Shanghai') AS hour_local,
               o.observed_at,
               (o.measurements ->> 'mosquito_total')::numeric AS value
        FROM affected AS a
        JOIN core.observation AS o USING (device_id, observation_day, point_no, is_demo)
    )
    INSERT INTO analysis.hourly_stats (
        device_id, hour_local, point_no, is_demo, max_value, valid_count, reported_count, max_observed_at
    )
    SELECT device_id, hour_local, point_no, is_demo, max(value), count(value), count(*),
           (array_agg(observed_at ORDER BY value DESC, observed_at)
               FILTER (WHERE value IS NOT NULL))[1]
    FROM samples
    GROUP BY device_id, hour_local, point_no, is_demo
    ON CONFLICT (device_id, hour_local, point_no, is_demo) DO UPDATE
    SET max_value = excluded.max_value, valid_count = excluded.valid_count,
        reported_count = excluded.reported_count, max_observed_at = excluded.max_observed_at;
$hourly$;

REVOKE ALL ON FUNCTION analysis.refresh_hourly_stats(bigint) FROM PUBLIC;

-- Backfill hours in a database initialized before hourly statistics existed.
SELECT pg_advisory_xact_lock(773031, 1);
SELECT analysis.refresh_hourly_stats(NULL);

CREATE OR REPLACE FUNCTION raw.ingest_snapshot(payload jsonb)
RETURNS jsonb
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $function$
DECLARE
    source_key text := payload ->> 'source_id';
    content_hash text := payload ->> 'sha256';
    current_batch bigint;
    archived_row record;
    mapped_device core.device%ROWTYPE;
    observed_local timestamp;
    observation_time timestamptz;
    readings jsonb;
    existing_readings jsonb;
    row_status text;
    row_reason text;
    inserted_this_run integer := 0;
BEGIN
    -- Serialize ingest and daily rollups even if two schedules overlap.
    PERFORM pg_advisory_xact_lock(773031, 1);

    SELECT batch_id INTO current_batch FROM raw.import_batch
    WHERE source_id = source_key AND sha256 = content_hash;

    IF current_batch IS NULL THEN
        INSERT INTO raw.import_batch (source_id, sha256, original_csv)
        VALUES (source_key, content_hash, decode(payload ->> 'original_csv', 'base64'))
        RETURNING batch_id INTO current_batch;

        INSERT INTO raw.observation_import (batch_id, line_no, cells, normalized, status, reason)
        SELECT current_batch, (item ->> 'line_no')::integer, item -> 'cells',
               item -> 'normalized',
               CASE WHEN item ? 'error' THEN 'invalid' ELSE 'pending' END,
               item ->> 'error'
        FROM jsonb_array_elements(payload -> 'records') AS entry(item);
    END IF;

    -- An unmapped machine can be registered later and recovered by replaying its CSV.
    FOR archived_row IN
        SELECT line_no, normalized FROM raw.observation_import
        WHERE batch_id = current_batch AND status IN ('pending', 'unmapped')
        ORDER BY line_no
    LOOP
        SELECT * INTO mapped_device FROM core.device
        WHERE source_id = source_key AND machine_no = archived_row.normalized ->> 'machine_no';
        row_reason := NULL;

        IF NOT FOUND THEN
            row_status := 'unmapped';
            row_reason := '机器编号未绑定点位';
        ELSE
            observed_local := (archived_row.normalized ->> 'observed_local')::timestamp;
            observation_time := observed_local AT TIME ZONE 'Asia/Shanghai';
            readings := archived_row.normalized -> 'measurements';

            INSERT INTO core.observation (
                device_id, observed_at, observation_day, point_no, is_demo,
                measurements, batch_id, line_no
            )
            VALUES (
                mapped_device.device_id, observation_time, observed_local::date,
                mapped_device.point_no, mapped_device.is_demo, readings,
                current_batch, archived_row.line_no
            )
            ON CONFLICT (device_id, observed_at) DO NOTHING;

            IF FOUND THEN
                row_status := 'imported';
                inserted_this_run := inserted_this_run + 1;
            ELSE
                SELECT measurements INTO existing_readings FROM core.observation
                WHERE device_id = mapped_device.device_id AND observed_at = observation_time;
                IF existing_readings = readings THEN
                    row_status := 'duplicate';
                ELSE
                    row_status := 'conflict';
                    row_reason := '同设备同时间已存在不同读数';
                END IF;
            END IF;
        END IF;

        UPDATE raw.observation_import
        SET status = row_status, reason = row_reason
        WHERE batch_id = current_batch AND line_no = archived_row.line_no;
    END LOOP;

    IF inserted_this_run > 0 THEN
        -- Recompute affected dates from all observations, including earlier batches.
        WITH affected AS (
            SELECT DISTINCT device_id, observation_day, point_no, is_demo
            FROM core.observation WHERE batch_id = current_batch
        ),
        samples AS (
            SELECT o.device_id, o.observation_day, o.point_no, o.is_demo,
                   m.key AS metric, (m.value #>> '{}')::numeric AS value
            FROM affected AS a
            JOIN core.observation AS o USING (device_id, observation_day, point_no, is_demo)
            CROSS JOIN LATERAL jsonb_each(o.measurements) AS m(key, value)
        )
        INSERT INTO analysis.observation_stats (
            device_id, observation_day, point_no, is_demo, metric,
            reported_count, valid_count, missing_value_count,
            mean_value, median_value, sum_value
        )
        SELECT device_id, observation_day, point_no, is_demo, metric,
               count(*), count(value), count(*) - count(value),
               avg(value), percentile_cont(0.5) WITHIN GROUP (ORDER BY value::double precision),
               CASE WHEN metric IN (
                   'mosquito_total', 'aedes_male', 'aedes_female', 'culex_male',
                   'culex_female', 'anopheles_male', 'anopheles_female'
               ) THEN sum(value) END
        FROM samples
        GROUP BY device_id, observation_day, point_no, is_demo, metric
        ON CONFLICT (device_id, observation_day, point_no, is_demo, metric) DO UPDATE
        SET reported_count = excluded.reported_count, valid_count = excluded.valid_count,
            missing_value_count = excluded.missing_value_count, mean_value = excluded.mean_value,
            median_value = excluded.median_value, sum_value = excluded.sum_value,
            calculated_at = now();

        PERFORM analysis.refresh_hourly_stats(current_batch);
    END IF;

    RETURN (
        SELECT jsonb_build_object(
            'batch_id', current_batch, 'rows', count(*), 'inserted', inserted_this_run,
            'duplicates', count(*) FILTER (WHERE status = 'duplicate'),
            'invalid', count(*) FILTER (WHERE status = 'invalid'),
            'unmapped', count(*) FILTER (WHERE status = 'unmapped'),
            'conflict', count(*) FILTER (WHERE status = 'conflict')
        )
        FROM raw.observation_import WHERE batch_id = current_batch
    );
END;
$function$;

REVOKE ALL ON FUNCTION raw.ingest_snapshot(jsonb) FROM PUBLIC;

COMMIT;
