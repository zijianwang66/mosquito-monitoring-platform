"""Generate nine synthetic observations per ten-minute interval."""

import argparse
import csv
import os
import random
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

from scan import HEADERS


SOURCE = "simulation"
SHANGHAI = timezone(timedelta(hours=8))
INTERVAL = 600
ROOT = Path(__file__).resolve().parent


def generate(folder, slot):
    observed = datetime.fromtimestamp(slot, SHANGHAI)
    path = folder / f"{observed:%Y%m%d-%H%M}.csv"
    randomizer = random.Random(slot)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(HEADERS)
        for point in range(1, 10):
            counts = [randomizer.randint(0, point + 2) for _ in range(6)]
            writer.writerow([
                observed.strftime("%Y/%m/%d %H:%M"), f"SIM-P{point:02d}", "九点模拟",
                *counts, round(randomizer.uniform(23, 29), 1),
                round(randomizer.uniform(55, 80), 1), round(randomizer.uniform(0, 3), 1), "",
            ])
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cluster", type=Path, required=True, help="本机监控预览的临时数据库目录")
    parser.add_argument("--psql", required=True)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    cluster = args.cluster.resolve()
    if cluster.name != "cluster" or not cluster.parent.name.startswith("mosquito-preview-"):
        parser.error("请选择本机监控预览生成的临时数据库目录")
    port = int((cluster / "postmaster.pid").read_text(encoding="utf-8").splitlines()[3])
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("PG")}
    env.update(PGHOST="127.0.0.1", PGPORT=str(port), PGUSER="preview",
               PGDATABASE="postgres", PGCLIENTENCODING="UTF8", PGCONNECT_TIMEOUT="5")
    sql = [args.psql, "-XqAtw", "-v", "ON_ERROR_STOP=1", "-c"]
    actual = subprocess.check_output([*sql, "SHOW data_directory"], env=env, text=True, encoding="utf-8")
    if Path(actual.strip()).resolve() != cluster:
        parser.error("连接的实例与预览数据库目录不一致")
    subprocess.run([*sql, """
        INSERT INTO core.device (source_id, machine_no, point_no, is_demo)
        SELECT 'simulation', 'SIM-P' || lpad(point::text, 2, '0'), point, true
        FROM generate_series(1, 9) AS point
        ON CONFLICT (source_id, machine_no) DO NOTHING
    """], env=env, check=True)
    folder = cluster.parent / "simulation-csv"
    folder.mkdir(exist_ok=True)
    while True:
        slot = int(time.time()) // INTERVAL * INTERVAL
        path = generate(folder, slot)
        result = subprocess.run([
            sys.executable, "-B", str(ROOT / "scan.py"), "--file", str(path),
            "--source-id", SOURCE, "--psql", args.psql, "--track-ops", "--check-quality",
        ], env=env)
        print(f"{path.name} · 9 observations · exit {result.returncode}", flush=True)
        if args.once:
            return result.returncode
        time.sleep(max(0, slot + INTERVAL - time.time()))


if __name__ == "__main__":
    raise SystemExit(main())
