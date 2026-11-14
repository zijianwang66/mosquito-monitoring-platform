"""Read-only API for the mosquito data operations dashboard."""

import argparse
import json
import os
import re
import subprocess
from pathlib import Path

from flask import Flask, jsonify, render_template, request


MAX_ID = 2**63 - 1


def read_database(psql, service, query):
    env = dict(os.environ, PGCLIENTENCODING="UTF8", PGCONNECT_TIMEOUT="5")
    if service:
        env["PGSERVICE"] = service
    script = (
        "BEGIN READ ONLY; SET LOCAL statement_timeout = '8s'; "
        "SET LOCAL TIME ZONE 'UTC'; " + query + "; COMMIT;"
    )
    result = subprocess.run(
        [psql, "-X", "-qAt", "-w", "-v", "ON_ERROR_STOP=1", "-f", "-"],
        input=script.encode("utf-8"), capture_output=True, env=env,
        timeout=15, check=True,
    )
    return json.loads(result.stdout)


def bounded_integer(value, minimum, maximum):
    if not re.fullmatch(r"[0-9]{1,19}", value):
        raise ValueError
    number = int(value)
    if not minimum <= number <= maximum:
        raise ValueError
    return number


def create_app(psql="psql", service=None):
    app = Flask(__name__, root_path=str(Path(__file__).resolve().parent))
    app.json.ensure_ascii = False

    def query(sql):
        try:
            return read_database(psql, service, sql)
        except (OSError, subprocess.SubprocessError, ValueError):
            app.logger.warning("OPS database query failed")
            return None

    @app.after_request
    def disable_cache(response):
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/")
    def dashboard():
        return render_template("ops.html")

    @app.get("/api/ops/simulation")
    def simulation():
        rows = query("""
            SELECT coalesce(json_agg(s ORDER BY observed_at DESC, point_no), '[]'::json)
            FROM (
                SELECT o.point_no, o.observed_at, i.batch_id::text AS batch_id,
                       i.line_no, i.normalized -> 'measurements' AS measurements
                FROM raw.observation_import AS i
                JOIN raw.import_batch AS b USING (batch_id)
                JOIN core.observation AS o USING (batch_id, line_no)
                WHERE b.source_id = 'simulation' AND o.is_demo
                ORDER BY o.observed_at DESC, o.point_no
                LIMIT 216
            ) AS s
        """)
        if rows is None:
            return jsonify(error="database_unavailable"), 503
        return jsonify(items=rows)

    @app.get("/api/ops/simulation/hourly")
    def simulation_hourly():
        day = request.args.get("date")
        if day is not None and not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", day):
            return jsonify(error="invalid_date"), 400
        day_sql = f"'{day}'" if day else "NULL"
        payload = query(f"""
            WITH available AS (
                SELECT max(hour_local) AS latest
                FROM app.point_hourly_stats WHERE source_id = 'simulation' AND is_demo
            )
            SELECT json_build_object(
                'latest', latest,
                'rows', coalesce((SELECT json_agg(r ORDER BY hour, point)
                    FROM (SELECT point_no AS point, extract(hour FROM hour_local)::int AS hour,
                                 max_value AS value
                          FROM app.point_hourly_stats
                          WHERE source_id = 'simulation' AND is_demo
                            AND hour_local::date::text = coalesce({day_sql}, latest::date::text)
                    ) AS r), '[]'::json))
            FROM available
        """)
        response = jsonify(payload if payload is not None else {"error": "database_unavailable"})
        response.status_code = 200 if payload is not None else 503
        if request.headers.get("Origin") == "null":
            response.headers["Access-Control-Allow-Origin"] = "null"
        response.vary.add("Origin")
        return response

    @app.get("/api/ops/runs")
    def list_runs():
        status = request.args.get("status")
        if status is not None and status not in ("running", "succeeded", "failed"):
            return jsonify(error="invalid_status"), 400
        try:
            limit = bounded_integer(request.args.get("limit", "25"), 1, 100)
            before = request.args.get("before")
            before = bounded_integer(before, 1, MAX_ID) if before is not None else None
        except ValueError:
            return jsonify(error="invalid_pagination"), 400

        conditions = ["TRUE"]
        if status is not None:
            conditions.append(f"status = '{status}'")
        if before is not None:
            conditions.append(f"run_id < {before}")
        rows = query(f"""
            SELECT coalesce(json_agg(r ORDER BY sort_id DESC), '[]'::json)
            FROM (
                SELECT run_id AS sort_id, run_id::text AS run_id,
                       batch_id::text AS batch_id, status, started_at, finished_at, failure_reason
                FROM ops.batch_run WHERE {" AND ".join(conditions)}
                ORDER BY sort_id DESC LIMIT {limit + 1}
            ) AS r
        """)
        if rows is None:
            return jsonify(error="database_unavailable"), 503
        items = rows[:limit]
        for item in items:
            del item["sort_id"]
        return jsonify(items=items, next_before=items[-1]["run_id"] if len(rows) > limit else None)

    @app.get("/api/ops/runs/<run_id>")
    def run_detail(run_id):
        try:
            number = bounded_integer(run_id, 1, MAX_ID)
        except ValueError:
            return jsonify(error="invalid_run_id"), 400
        rows = query(f"""
            SELECT coalesce(json_agg(detail), '[]'::json) FROM (
                SELECT r.run_id::text AS run_id, r.batch_id::text AS batch_id,
                       r.status, r.started_at, r.finished_at, r.failure_reason,
                       (SELECT coalesce(json_agg(t ORDER BY t.sort_id), '[]'::json)
                        FROM (SELECT task_id AS sort_id, task_id::text AS task_id, layer, task_name,
                                     status, started_at, finished_at, input_rows, output_rows,
                                     affected_from, affected_until, failure_reason
                              FROM ops.task_log WHERE run_id = r.run_id) AS t) AS tasks,
                       (SELECT coalesce(json_agg(q ORDER BY q.sort_id), '[]'::json)
                        FROM (SELECT check_id AS sort_id, check_id::text AS check_id,
                                     task_id::text AS task_id, rule_code, result, expected_value,
                                     actual_value, detail, checked_at
                              FROM ops.quality_report WHERE run_id = r.run_id) AS q) AS quality_checks
                FROM ops.batch_run AS r WHERE r.run_id = {number}
            ) AS detail
        """)
        if rows is None:
            return jsonify(error="database_unavailable"), 503
        if not rows:
            return jsonify(error="run_not_found"), 404
        detail = rows[0]
        for item in detail["tasks"] + detail["quality_checks"]:
            del item["sort_id"]
        return jsonify(detail)

    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--psql", default="psql")
    parser.add_argument("--pg-service")
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    create_app(args.psql, args.pg_service).run(host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
