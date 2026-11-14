BEGIN;

CREATE OR REPLACE FUNCTION ops.process_snapshot(target_run bigint, payload jsonb)
RETURNS jsonb
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $track$
DECLARE
    report jsonb;
    raw_batch bigint;
    inserted_rows bigint;
    archived_rows bigint;
    processing_started timestamptz := clock_timestamp();
    processing_finished timestamptz;
    window_start timestamptz;
    window_end timestamptz;
BEGIN
    PERFORM 1 FROM ops.batch_run
    WHERE run_id = target_run AND status = 'running'
    FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'OPS run % is missing or closed', target_run;
    END IF;

    report := raw.ingest_snapshot(payload);
    raw_batch := (report ->> 'batch_id')::bigint;
    inserted_rows := (report ->> 'inserted')::bigint;
    archived_rows := (report ->> 'rows')::bigint;
    processing_finished := clock_timestamp();

    INSERT INTO ops.task_log
        (run_id, layer, task_name, status, started_at, finished_at, input_rows, output_rows)
    VALUES
        (target_run, 'ODS', 'archive_or_reuse_snapshot', 'succeeded',
         processing_started, processing_finished, jsonb_array_length(payload -> 'records'), archived_rows),
        (target_run, 'DWD', 'map_and_insert_observations', 'succeeded',
         processing_started, processing_finished, archived_rows, inserted_rows);

    IF inserted_rows > 0 THEN
        SELECT min(observation_day)::timestamp AT TIME ZONE 'Asia/Shanghai',
               (max(observation_day) + 1)::timestamp AT TIME ZONE 'Asia/Shanghai'
        INTO window_start, window_end
        FROM core.observation WHERE batch_id = raw_batch;

        INSERT INTO ops.task_log
            (run_id, layer, task_name, status, started_at, finished_at, affected_from, affected_until)
        VALUES
            (target_run, 'DWS', 'refresh_daily_and_hourly_stats', 'succeeded',
             processing_started, processing_finished, window_start, window_end);
    END IF;

    -- ADS reads the committed aggregates through existing views.
    UPDATE ops.batch_run
    SET batch_id = raw_batch, status = 'succeeded', finished_at = clock_timestamp()
    WHERE run_id = target_run;

    RETURN report || jsonb_build_object('run_id', target_run);
END;
$track$;

REVOKE ALL ON FUNCTION ops.process_snapshot(bigint, jsonb) FROM PUBLIC;

COMMIT;
