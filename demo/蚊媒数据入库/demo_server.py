"""Serve the dashboard and its read-only daily statistics API on localhost."""

import argparse
from datetime import date
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import subprocess
from urllib.parse import parse_qs, unquote, urlsplit


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
ASSETS = {
    "/": ("蚊媒监测驾驶舱.html", "text/html; charset=utf-8"),
    "/蚊媒监测驾驶舱.html": ("蚊媒监测驾驶舱.html", "text/html; charset=utf-8"),
    "/蚊媒监测设备总览.html": ("蚊媒监测设备总览.html", "text/html; charset=utf-8"),
    "/蚊媒监测平台-居民首页.html": ("蚊媒监测平台-居民首页.html", "text/html; charset=utf-8"),
    "/住区2米网格数据.js": ("住区2米网格数据.js", "text/javascript; charset=utf-8"),
    "/住区CAD底图.png": ("住区CAD底图.png", "image/png"),
    "/住区播报员.webp": ("住区播报员.webp", "image/webp"),
    "/蚊媒数据入库/demo-trend.js": ("蚊媒数据入库/demo-trend.js", "text/javascript; charset=utf-8"),
    "/蚊媒数据入库/demo-hourly.js": ("蚊媒数据入库/demo-hourly.js", "text/javascript; charset=utf-8"),
    "/蚊媒数据入库/demo-alerts.js": ("蚊媒数据入库/demo-alerts.js", "text/javascript; charset=utf-8"),
    "/蚊媒数据入库/demo-point-layout.js": ("蚊媒数据入库/demo-point-layout.js", "text/javascript; charset=utf-8"),
}


def date_range(query):
    parameters = parse_qs(query, keep_blank_values=True)
    start = date.fromisoformat(parameters.get("from", ["2026-11-01"])[0])
    end = date.fromisoformat(parameters.get("to", ["2026-11-02"])[0])
    raw_point = parameters.get("point", ["7"])[0]
    if not re.fullmatch(r"[1-7]", raw_point) or not 0 <= (end - start).days <= 61:
        raise ValueError("点位须为 1–7，日期范围最长 62 天。")
    return int(raw_point), start, end


def daily_query(point, start, end):
    # Route validation supplies only an integer point and canonical ISO dates.
    return f"""
SELECT coalesce(json_agg(d ORDER BY day), '[]'::json)
FROM (
    SELECT observation_day AS day, mean_value AS mean, median_value AS median,
           valid_count, missing_value_count
    FROM app.point_daily_stats
    WHERE source_id = 'demo' AND is_demo AND metric = 'mosquito_total'
      AND point_no = {point:d} AND machine_no = 'P{point:02d}-DEMO'
      AND observation_day BETWEEN DATE '{start.isoformat()}' AND DATE '{end.isoformat()}'
) AS d;
"""


def hourly_query(day):
    return f"""
SELECT coalesce(json_agg(h ORDER BY hour, point), '[]'::json)
FROM (
    SELECT point_no AS point, extract(hour FROM hour_local)::integer AS hour,
           max_value AS value, valid_count
    FROM app.point_hourly_stats
    WHERE source_id = 'demo' AND is_demo AND point_no BETWEEN 1 AND 7
      AND machine_no = 'P' || lpad(point_no::text, 2, '0') || '-DEMO'
      AND hour_local >= DATE '{day.isoformat()}'
      AND hour_local < DATE '{day.isoformat()}' + 1
) AS h;
"""


def alerts_query(day):
    return f"""
SELECT coalesce(json_agg(a ORDER BY observed_at DESC, point), '[]'::json)
FROM (
    SELECT point_no AS point, extract(hour FROM hour_local)::integer AS hour,
           to_char(observed_local, 'YYYY-MM-DD HH24:MI') AS observed_at, reading, threshold
    FROM app.demo_alerts
    WHERE hour_local >= DATE '{day.isoformat()}' AND hour_local < DATE '{day.isoformat()}' + 1
) AS a;
"""


def read_database(psql, query):
    result = subprocess.run(
        [psql, "-X", "-w", "-qAt", "-v", "ON_ERROR_STOP=1", "-c", query],
        env=dict(os.environ, PGCLIENTENCODING="UTF8", PGCONNECT_TIMEOUT="4"),
        capture_output=True, timeout=10, check=True,
    )
    return json.loads(result.stdout)


class DemoHandler(BaseHTTPRequestHandler):
    def __init__(self, *args, psql, snapshot, hourly_snapshot, **kwargs):
        self.psql = psql
        self.snapshot = snapshot
        self.hourly_snapshot = hourly_snapshot
        super().__init__(*args, **kwargs)

    def send_content(self, status, content_type, body):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, status, data):
        self.send_content(status, "application/json; charset=utf-8",
                          json.dumps(data, ensure_ascii=False).encode("utf-8"))

    def do_GET(self):
        requested = urlsplit(self.path)
        path = unquote(requested.path)
        if path == "/api/demo/daily-stats":
            self.serve_daily(requested.query)
            return
        if path == "/api/demo/hourly-stats":
            self.serve_day(requested.query, hourly_query, "rows")
            return
        if path == "/api/demo/alerts":
            self.serve_day(requested.query, alerts_query, "alerts")
            return
        asset = ASSETS.get(path)
        if asset is None:
            self.send_error(404)
            return
        filename, content_type = asset
        try:
            body = (ROOT / filename).read_bytes()
        except OSError:
            self.send_error(404)
            return
        self.send_content(200, content_type, body)

    def serve_daily(self, query):
        try:
            point, start, end = date_range(query)
        except ValueError:
            self.send_json(400, {"error": "请提供点位 1–7 和有效的日期范围（最长 62 天）。"})
            return

        try:
            if self.snapshot is None:
                rows = read_database(self.psql, daily_query(point, start, end))
            else:
                rows = [
                    row for row in self.snapshot.get(str(point), [])
                    if start.isoformat() <= row["day"] <= end.isoformat()
                ]
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            self.log_error("Daily statistics query failed: %s", type(error).__name__)
            self.send_json(503, {"error": "联调数据库暂不可用，请检查数据库连接后重试。"})
            return
        self.send_json(200, {
            "point": point,
            "mode": "snapshot" if self.snapshot is not None else "database",
            "rows": rows,
        })

    def serve_day(self, query, make_query, collection):
        try:
            day = date.fromisoformat(parse_qs(query, keep_blank_values=True).get("date", ["2026-11-01"])[0])
        except ValueError:
            self.send_json(400, {"error": "请输入有效的日期。"})
            return
        try:
            rows = (read_database(self.psql, make_query(day)) if self.hourly_snapshot is None
                    else self.hourly_snapshot.get(day.isoformat(), {}).get(collection, []))
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            self.log_error("Daily %s query failed: %s", collection, type(error).__name__)
            self.send_json(503, {"error": "监测数据暂不可用，请检查数据库后重试。"})
            return
        self.send_json(200, {"date": day.isoformat(), collection: rows,
                             "mode": "database" if self.hourly_snapshot is None else "snapshot"})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--psql", default="psql")
    parser.add_argument("--snapshot", action="store_true",
                        help="Use the checked-in export from the demo database")
    args = parser.parse_args()
    if args.snapshot:
        snapshot = json.loads((HERE / "demo-daily-snapshot.json").read_text(encoding="utf-8"))
        hourly_snapshot = json.loads((HERE / "demo-hourly-snapshot.json").read_text(encoding="utf-8"))
    else:
        snapshot = None
        hourly_snapshot = None
        if not os.environ.get("PGSERVICE"):
            os.environ["PGDATABASE"] = "mosquito_monitoring_test"
    handler = partial(DemoHandler, psql=args.psql, snapshot=snapshot, hourly_snapshot=hourly_snapshot)
    with ThreadingHTTPServer(("127.0.0.1", args.port), handler) as server:
        print(f"Demo dashboard: http://127.0.0.1:{server.server_port}/#point7-daily", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
