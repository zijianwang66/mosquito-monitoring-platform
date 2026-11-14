"""Fetch one CSV snapshot and import it into PostgreSQL."""

import argparse
import base64
import csv
from datetime import datetime
from hashlib import sha256
from http.client import HTTPException
import io
import json
import logging
from logging.handlers import RotatingFileHandler
import math
import os
from pathlib import Path
import re
import subprocess
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


MAX_CSV_BYTES = 4 * 1024 * 1024
HEADERS = (
    "记录时间", "机器编号", "分组名称", "雄伊蚊数", "雌伊蚊数", "雄库蚊数",
    "雌库蚊数", "雄按蚊数", "雌按蚊数", "温度(°C)", "湿度(%)", "风速(m/s)", "机器地址",
)
COUNT_METRICS = (
    "aedes_male", "aedes_female", "culex_male",
    "culex_female", "anopheles_male", "anopheles_female",
)
ENV_METRICS = ("temperature_c", "humidity_pct", "wind_speed_mps")


class ScanError(Exception):
    """An actionable failure without sensitive request data."""


class DirectHTTPS(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # A redirected request could send the bearer token to another host.
        raise ScanError(f"云端返回重定向 HTTP {code}，请配置最终 CSV 地址")


def read_csv_bytes(stream):
    data = stream.read(MAX_CSV_BYTES + 1)
    if not data or len(data) > MAX_CSV_BYTES:
        raise ScanError("CSV 为空或超过 4 MiB。请配置较短的导出时间窗")
    return data


def download_csv(url, token):
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username is not None:
        raise ScanError("csv_url 必须是有效的 HTTPS CSV 地址")
    headers = {"Accept": "text/csv", "User-Agent": "mosquito-csv-import/1"}
    if token:
        if "\r" in token or "\n" in token:
            raise ScanError("鉴权令牌包含无效换行")
        headers["Authorization"] = f"Bearer {token}"
    opener = build_opener(DirectHTTPS())
    for attempt in range(3):
        try:
            with opener.open(Request(url, headers=headers), timeout=30) as response:
                if response.status != 200:
                    raise ScanError(f"云端返回 HTTP {response.status}，需要完整的 CSV 响应")
                content = read_csv_bytes(response)
                content_length = response.headers.get("Content-Length")
                if content_length is not None and content_length.strip() != str(len(content)):
                    raise HTTPException("Incomplete CSV response")
                return content
        except HTTPError as error:
            status = error.code
            error.close()
            if status not in (429, 500, 502, 503, 504) or attempt == 2:
                raise ScanError(f"云端返回 HTTP {status}") from None
        except (URLError, OSError, HTTPException) as error:
            if attempt == 2:
                raise ScanError(f"云端读取失败（{type(error).__name__}）") from None
        time.sleep(3 * (attempt + 1))
    raise AssertionError("retry loop exhausted")


def decode_csv(content):
    for encoding in ("utf-8-sig", "gbk"):
        try:
            text = content.decode(encoding)
        except UnicodeDecodeError:
            continue
        if "\0" in text:
            raise ScanError("CSV 包含 NUL 字符")
        return text
    raise ScanError("CSV 编码无法识别，支持 UTF-8 和 GBK")


def read_count(cell):
    if not cell:
        return None
    if not re.fullmatch(r"[0-9]{1,10}", cell) or int(cell) > 2147483647:
        raise ValueError("须为非负整数且不超过 2147483647")
    return int(cell)


def read_environment(cell, minimum=None, maximum=None):
    if not cell:
        return None
    if not re.fullmatch(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)", cell):
        raise ValueError("须为十进制数")
    value = float(cell)
    if not math.isfinite(value) or abs(value) > 1e6:
        raise ValueError("数值超出允许范围")
    if minimum is not None and value < minimum:
        raise ValueError(f"数值须大于或等于 {minimum}")
    if maximum is not None and value > maximum:
        raise ValueError(f"数值须小于或等于 {maximum}")
    return value


def normalize(cells):
    if len(cells) != len(HEADERS):
        raise ValueError(f"应有 {len(HEADERS)} 列，实际 {len(cells)} 列")
    timestamp, machine, *_ = (value.strip() for value in cells)
    if not machine or len(machine) > 128:
        raise ValueError("机器编号不能为空或超过 128 字符")
    try:
        observed_local = datetime.strptime(timestamp, "%Y/%m/%d %H:%M")
    except ValueError:
        raise ValueError("记录时间须采用 YYYY/M/D H:MM") from None

    measurements = {}
    for index, metric in enumerate(COUNT_METRICS, start=3):
        try:
            measurements[metric] = read_count(cells[index].strip())
        except ValueError as error:
            raise ValueError(f"{HEADERS[index]}{error}") from None

    for index, (metric, limits) in enumerate(
        zip(ENV_METRICS, ((None, None), (0, 100), (0, None))), start=9
    ):
        try:
            measurements[metric] = read_environment(cells[index].strip(), *limits)
        except ValueError as error:
            raise ValueError(f"{HEADERS[index]}{error}") from None

    counts = [measurements[key] for key in COUNT_METRICS]
    measurements["mosquito_total"] = None if None in counts else sum(counts)
    return {
        "machine_no": machine,
        "observed_local": observed_local.isoformat(timespec="minutes"),
        "measurements": measurements,
    }


def parse_snapshot(content):
    reader = csv.reader(io.StringIO(decode_csv(content), newline=""), strict=True)
    try:
        if tuple(value.strip() for value in next(reader, ())) != HEADERS:
            raise ScanError("CSV 表头不符合机器导出的 13 列格式")
        records = []
        while True:
            line_no = reader.line_num + 1
            cells = next(reader, None)
            if cells is None:
                break
            if not cells:
                continue
            record = {"line_no": line_no, "cells": cells}
            try:
                record["normalized"] = normalize(cells)
            except ValueError as error:
                record["error"] = str(error)
            records.append(record)
    except csv.Error as error:
        raise ScanError(f"CSV 引号或字段格式错误，位置约在第 {reader.line_num} 行") from error
    return records


def make_sql(source_id, content, records, run_id=None):
    payload = {
        "source_id": source_id,
        "sha256": sha256(content).hexdigest(),
        "original_csv": base64.b64encode(content).decode("ascii"),
        "records": records,
    }
    # Base64 emits only ASCII letters, digits, +, / and =. CSV cells never
    # enter the SQL as executable text.
    encoded = base64.b64encode(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    function = b"raw.ingest_snapshot(" if run_id is None else (
        b"ops.process_snapshot(" + str(int(run_id)).encode("ascii") + b", "
    )
    return (
        b"BEGIN;\nSET LOCAL lock_timeout = '30s';\n"
        b"SET LOCAL statement_timeout = '180s';\n"
        b"SELECT " + function + b"convert_from(decode('"
        + encoded + b"', 'base64'), 'UTF8')::jsonb);\nCOMMIT;\n"
    )


def write_snapshot(psql, service, sql):
    env = dict(os.environ, PGCLIENTENCODING="UTF8", PGCONNECT_TIMEOUT="10")
    if service:
        env["PGSERVICE"] = service
    try:
        result = subprocess.run(
            [psql, "-X", "-qAt", "-w", "-v", "ON_ERROR_STOP=1",
             "-v", "VERBOSITY=terse", "-v", "SHOW_CONTEXT=never", "-f", "-"],
            input=sql, capture_output=True, timeout=210, env=env, check=False,
        )
    except FileNotFoundError:
        raise ScanError("找不到 psql，请安装 PostgreSQL 客户端并配置 psql 路径") from None
    except subprocess.TimeoutExpired:
        raise ScanError("数据库执行超时。请检查数据库或等待下一轮扫描重试") from None
    if result.returncode:
        message = result.stderr.decode("utf-8", errors="replace").strip()
        raise ScanError(f"数据库写入失败（psql {result.returncode}）\n{message}")
    return json.loads(result.stdout)


def load_records(args, config):
    if args.file:
        with args.file.open("rb") as stream:
            content = read_csv_bytes(stream)
    else:
        url = config.get("csv_url")
        if not url:
            raise ScanError("云端配置缺少 csv_url")
        token = os.environ.get("MOSQUITO_CSV_TOKEN") or config.get("bearer_token")
        content = download_csv(url, token)
    return content, parse_snapshot(content)


def tracked_snapshot(args, config, source_id, write):
    run_id = int(write(b"INSERT INTO ops.batch_run DEFAULT VALUES RETURNING run_id;"))
    stage = "source"
    try:
        content, records = load_records(args, config)
        stage = "database"
        return write(make_sql(source_id, content, records, run_id))
    except (ScanError, OSError, ValueError):
        reason = {
            "source": b"CSV acquisition or parsing failed",
            "database": b"Database processing failed or completion could not be confirmed",
        }[stage]
        try:
            write(
                b"WITH closed AS (UPDATE ops.batch_run SET status = 'failed', finished_at = clock_timestamp(), "
                b"failure_reason = '" + reason + b"' WHERE status = 'running' AND run_id = "
                + str(run_id).encode("ascii") + b" RETURNING run_id) SELECT count(*) FROM closed;"
            )
        except (ScanError, OSError, ValueError):
            logging.warning("运行 %d 未能确认失败状态，请查询 ops.batch_run", run_id)
        raise


def run(args):
    config = {}
    if args.config:
        with args.config.open(encoding="utf-8-sig") as stream:
            config = json.load(stream)
        if not isinstance(config, dict) or any(not isinstance(v, str) for v in config.values()):
            raise ScanError("配置应为 JSON 对象，配置值应为字符串")

    source_id = args.source_id or config.get("source_id", "")
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", source_id):
        raise ScanError("source_id 须为 1–64 位字母、数字、下划线或连字符")
    if args.check:
        _, records = load_records(args, config)
        valid = [row["normalized"] for row in records if "normalized" in row]
        logging.info("来源=%s 解析=%d 有效=%d 格式错误=%d",
                     source_id, len(records), len(valid), len(records) - len(valid))
        logging.info("机器编号=%s", ", ".join(sorted({row["machine_no"] for row in valid})))
        for row in records:
            if "error" in row:
                logging.warning("CSV 第 %d 行：%s", row["line_no"], row["error"])
        return 1 if len(valid) != len(records) else 0

    def write(sql):
        return write_snapshot(
            args.psql or config.get("psql", "psql"), config.get("pg_service"), sql,
        )

    if args.track_ops:
        report = tracked_snapshot(args, config, source_id, write)
    else:
        content, records = load_records(args, config)
        report = write(make_sql(source_id, content, records))
    logging.info("入库结果 %s", json.dumps(report, ensure_ascii=False))
    if args.check_quality:
        run_id = str(int(report["run_id"])).encode("ascii")
        try:
            quality = write(b"SELECT ops.check_run(" + run_id + b");")
        except (ScanError, OSError, ValueError):
            try:
                write(
                    b"WITH saved AS (INSERT INTO ops.quality_check "
                    b"(run_id, rule_code, result, detail) SELECT "
                    + run_id
                    + b", 'QUALITY_EXECUTION', 'error', 'Quality check failed to complete' "
                    b"WHERE NOT EXISTS (SELECT 1 FROM ops.quality_check WHERE run_id = "
                    + run_id + b") RETURNING check_id) SELECT count(*) FROM saved;"
                )
            except (ScanError, OSError, ValueError):
                logging.warning("运行 %s 未能写入质量检查执行错误", report["run_id"])
            raise ScanError(f"入库已提交，运行 {report['run_id']} 的质量检查未能确认完成") from None
        logging.info("质量检查 %s", json.dumps(quality, ensure_ascii=False))
        if any(quality.values()):
            return 2
    if any(report[key] for key in ("invalid", "unmapped", "conflict")):
        logging.warning("此批有待处理记录，按批次号查询 raw.observation_import")
        return 2
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--file", type=Path, help="本地 CSV，用于联调")
    source.add_argument("--config", type=Path, help="云端 CSV JSON 配置")
    parser.add_argument("--source-id", help="来源标识，对应数据库里的设备映射")
    parser.add_argument("--psql", help="psql 可执行文件路径")
    parser.add_argument("--check", action="store_true", help="只解析 CSV")
    parser.add_argument("--track-ops", action="store_true", help="记录 OPS 运行与分层任务")
    parser.add_argument("--check-quality", action="store_true", help="入库后执行质量检查与对账")
    parser.add_argument("--log-file", type=Path, help="定时运行日志，自动轮换")
    args = parser.parse_args()
    if args.check_quality and (not args.track_ops or args.check):
        parser.error("--check-quality 须与 --track-ops 一起用于实际入库")
    handlers = [logging.StreamHandler()]
    if args.log_file:
        args.log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(RotatingFileHandler(
            args.log_file, maxBytes=2_000_000, backupCount=3, encoding="utf-8",
        ))
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=handlers,
    )
    try:
        return run(args)
    except (ScanError, OSError, ValueError) as error:
        logging.error("%s", error)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
