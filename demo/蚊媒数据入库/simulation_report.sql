-- Run on the local preview database. Each row shows the latest simulated
-- observation at a point and the current ADS aggregates for its day and hour.
WITH latest AS (
    SELECT DISTINCT ON (o.device_id) o.*,
           date_trunc('hour', o.observed_at AT TIME ZONE 'Asia/Shanghai') AS hour_local
    FROM core.observation AS o
    JOIN core.device AS d USING (device_id)
    WHERE d.source_id = 'simulation' AND o.is_demo
    ORDER BY o.device_id, o.observed_at DESC
)
SELECT o.point_no, o.batch_id, o.line_no,
       o.observed_at AT TIME ZONE 'Asia/Shanghai' AS observed_local,
       o.measurements ->> 'mosquito_total' AS observation_total,
       h.max_value AS hourly_peak, h.valid_count AS hourly_valid,
       d.sum_value AS daily_total, d.valid_count AS daily_valid
FROM latest AS o
JOIN core.device AS device USING (device_id)
LEFT JOIN app.point_hourly_stats AS h
    ON (h.source_id, h.machine_no, h.point_no, h.is_demo, h.hour_local)
     = (device.source_id, device.machine_no, o.point_no, o.is_demo, o.hour_local)
LEFT JOIN app.point_daily_stats AS d
    ON (d.source_id, d.machine_no, d.point_no, d.is_demo, d.observation_day, d.metric)
     = (device.source_id, device.machine_no, o.point_no, o.is_demo, o.observation_day, 'mosquito_total')
ORDER BY o.point_no;
