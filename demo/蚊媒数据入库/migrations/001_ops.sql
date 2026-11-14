BEGIN;

CREATE SCHEMA IF NOT EXISTS ops;

-- A run can start before the raw batch exists. Link the batch after ingestion commits.
CREATE TABLE IF NOT EXISTS ops.batch_run (
    run_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    batch_id bigint REFERENCES raw.import_batch (batch_id),
    status text NOT NULL DEFAULT 'running'
        CHECK (status IN ('running', 'succeeded', 'failed')),
    started_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    finished_at timestamptz,
    failure_reason text,
    CHECK ((status = 'running') = (finished_at IS NULL)),
    CHECK (finished_at >= started_at),
    CHECK (status <> 'succeeded' OR batch_id IS NOT NULL),
    CHECK ((status = 'failed') = (failure_reason IS NOT NULL)),
    CHECK (failure_reason IS NULL OR btrim(failure_reason) <> '')
);

CREATE INDEX IF NOT EXISTS batch_run_batch_idx
    ON ops.batch_run (batch_id, started_at DESC);

CREATE TABLE IF NOT EXISTS ops.task_log (
    task_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id bigint NOT NULL REFERENCES ops.batch_run (run_id),
    layer text NOT NULL CHECK (layer IN ('ODS', 'DWD', 'DWS', 'ADS')),
    task_name text NOT NULL CHECK (btrim(task_name) <> ''),
    status text NOT NULL DEFAULT 'running'
        CHECK (status IN ('running', 'succeeded', 'failed')),
    started_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    finished_at timestamptz,
    input_rows bigint CHECK (input_rows >= 0),
    output_rows bigint CHECK (output_rows >= 0),
    affected_from timestamptz,
    affected_until timestamptz,
    failure_reason text,
    UNIQUE (run_id, task_id),
    CHECK ((status = 'running') = (finished_at IS NULL)),
    CHECK (finished_at >= started_at),
    CHECK ((affected_from IS NULL) = (affected_until IS NULL)),
    CHECK (affected_until > affected_from),
    CHECK ((status = 'failed') = (failure_reason IS NOT NULL)),
    CHECK (failure_reason IS NULL OR btrim(failure_reason) <> '')
);

CREATE TABLE IF NOT EXISTS ops.quality_check (
    check_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id bigint NOT NULL REFERENCES ops.batch_run (run_id),
    task_id bigint,
    rule_code text NOT NULL CHECK (btrim(rule_code) <> ''),
    result text NOT NULL CHECK (result IN ('passed', 'failed', 'error')),
    expected_value jsonb,
    actual_value jsonb,
    detail text,
    checked_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (run_id, task_id) REFERENCES ops.task_log (run_id, task_id)
);

CREATE INDEX IF NOT EXISTS quality_check_run_task_idx
    ON ops.quality_check (run_id, task_id, checked_at DESC);

COMMIT;
