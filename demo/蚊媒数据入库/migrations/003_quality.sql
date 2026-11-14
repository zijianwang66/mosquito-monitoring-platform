BEGIN;

CREATE OR REPLACE VIEW ops.hourly_reconciliation AS
WITH samples AS (
    SELECT device_id, point_no, is_demo, observed_at,
           date_trunc('hour', observed_at AT TIME ZONE 'Asia/Shanghai') AS hour_local,
           (measurements ->> 'mosquito_total')::numeric AS value
    FROM core.observation
), expected AS (
    SELECT device_id, point_no, is_demo, hour_local, count(*) AS reported_count,
           count(value) AS valid_count, max(value) AS max_value,
           (array_agg(observed_at ORDER BY value DESC, observed_at)
               FILTER (WHERE value IS NOT NULL))[1] AS max_observed_at
    FROM samples GROUP BY device_id, point_no, is_demo, hour_local
)
SELECT device_id, point_no, is_demo, hour_local,
       to_jsonb(e) AS expected_value, to_jsonb(a) AS actual_value
FROM expected AS e
FULL JOIN analysis.hourly_stats AS a USING (device_id, point_no, is_demo, hour_local)
WHERE to_jsonb(e) IS DISTINCT FROM to_jsonb(a);

CREATE OR REPLACE VIEW ops.daily_reconciliation AS
WITH expected AS (
    SELECT o.device_id, o.point_no, o.is_demo, o.observation_day, m.key AS metric,
           count(*) AS reported_count, count(v.value) AS valid_count,
           count(*) - count(v.value) AS missing_value_count, avg(v.value) AS mean_value,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY v.value::double precision) AS median_value,
           CASE WHEN m.key IN (
               'mosquito_total', 'aedes_male', 'aedes_female', 'culex_male',
               'culex_female', 'anopheles_male', 'anopheles_female'
           ) THEN sum(v.value) END AS sum_value
    FROM core.observation AS o
    CROSS JOIN LATERAL jsonb_each(o.measurements) AS m(key, value)
    CROSS JOIN LATERAL (SELECT (m.value #>> '{}')::numeric AS value) AS v
    GROUP BY o.device_id, o.point_no, o.is_demo, o.observation_day, m.key
)
SELECT device_id, point_no, is_demo, observation_day, metric,
       to_jsonb(e) AS expected_value, to_jsonb(a) - 'calculated_at' AS actual_value
FROM expected AS e
FULL JOIN analysis.observation_stats AS a USING (device_id, point_no, is_demo, observation_day, metric)
WHERE to_jsonb(e) IS DISTINCT FROM (to_jsonb(a) - 'calculated_at');

CREATE OR REPLACE FUNCTION ops.check_run(target_run bigint)
RETURNS jsonb
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $check$
DECLARE
    raw_batch bigint;
    report jsonb;
BEGIN
    SELECT batch_id INTO raw_batch FROM ops.batch_run
    WHERE run_id = target_run AND status = 'succeeded';
    IF NOT FOUND THEN
        RAISE EXCEPTION 'OPS run % has no committed batch', target_run;
    END IF;

    WITH checks(rule_code, issue_count) AS (
        SELECT 'IMPORT_INVALID', count(*) FROM raw.observation_import
        WHERE batch_id = raw_batch AND status = 'invalid'
        UNION ALL
        SELECT 'IMPORT_UNMAPPED', count(*) FROM raw.observation_import
        WHERE batch_id = raw_batch AND status = 'unmapped'
        UNION ALL
        SELECT 'IMPORT_CONFLICT', count(*) FROM raw.observation_import
        WHERE batch_id = raw_batch AND status = 'conflict'
        UNION ALL
        SELECT 'IMPORT_TRACE', count(*)
        FROM raw.observation_import AS i
        WHERE i.batch_id = raw_batch AND i.status = 'imported'
          AND NOT EXISTS (
              SELECT 1 FROM core.observation AS o
              WHERE o.batch_id = i.batch_id AND o.line_no = i.line_no
          )
        UNION ALL
        SELECT 'MISSING_METRIC_VALUES', count(*)
        FROM core.observation AS o
        CROSS JOIN unnest(ARRAY[
            'mosquito_total', 'aedes_male', 'aedes_female', 'culex_male',
            'culex_female', 'anopheles_male', 'anopheles_female',
            'temperature_c', 'humidity_pct', 'wind_speed_mps'
        ]) AS metric(key)
        WHERE o.batch_id = raw_batch AND o.measurements ->> metric.key IS NULL
        UNION ALL
        SELECT 'DAILY_RECONCILIATION', count(*) FROM ops.daily_reconciliation
        UNION ALL
        SELECT 'HOURLY_RECONCILIATION', count(*) FROM ops.hourly_reconciliation
    ), saved AS (
        INSERT INTO ops.quality_check (run_id, rule_code, result, expected_value, actual_value, detail)
        SELECT target_run, rule_code, CASE WHEN issue_count = 0 THEN 'passed' ELSE 'failed' END,
               to_jsonb(0), to_jsonb(issue_count),
               CASE WHEN rule_code IN ('DAILY_RECONCILIATION', 'HOURLY_RECONCILIATION')
                    THEN 'All observation periods' ELSE 'Run batch' END
        FROM checks
        RETURNING rule_code, actual_value
    )
    SELECT jsonb_object_agg(rule_code, actual_value) INTO report FROM saved;

    RETURN report;
END;
$check$;

REVOKE ALL ON FUNCTION ops.check_run(bigint) FROM PUBLIC;

CREATE OR REPLACE VIEW ops.quality_report AS
SELECT q.check_id, q.run_id, r.batch_id, q.task_id, q.rule_code,
       q.result, q.expected_value, q.actual_value, q.detail, q.checked_at
FROM ops.quality_check AS q
JOIN ops.batch_run AS r USING (run_id);

COMMIT;
