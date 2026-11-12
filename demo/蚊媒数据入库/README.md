# 蚊媒监测 CSV 自动入库

仪器按厂商配置向厂商云上报。项目后台定时获取云端提供的 CSV，再完成清洗、写库和日统计。每台设备只需在初次接入时登记机器编号与点位的对应关系。

如果厂商可以推送到项目服务器，可以由推送触发同样的入库流程。当前实现采用 **HTTPS CSV 拉取**。若只有网页登录导出、JSON 分页 API 或对象存储目录，需要根据厂商提供的正式接口调整下载入口。

## 1. 初始化 PostgreSQL

准备一个 PostgreSQL 数据库，建议先用单独的联调库 `mosquito_monitoring_test`。在本目录打开 PowerShell。以下示例使用这台机器 QGIS 自带的 PostgreSQL 客户端。

```powershell
$psql = 'C:\Program Files\QGIS 3.30.3\bin\psql.exe'
$env:PGCLIENTENCODING = 'UTF8'
& $psql -X -v ON_ERROR_STOP=1 -h localhost -U postgres -d mosquito_monitoring_test -f '.\schema.sql'
```

入库服务使用 Python 3.10+ 标准库和 `psql`，`schema.sql` 无需 PostGIS。PostGIS 的地图坐标由项目布点表管理。

| 层 | 对象 | 内容 |
|---|---|---|
| `raw` | `import_batch`、`observation_import` | CSV 原始字节、文件哈希、行号、原始字段和处理状态 |
| `core` | `device`、`observation` | 设备到点位映射、上海本地日、带时区时间、六类计数和环境观测 |
| `analysis` | `observation_stats` | 按设备、点位、日期和指标预计算的均值、中位数、样本数和计数合计 |
| `app` | `point_daily_stats` | 图表查询视图，带机器编号和模拟数据标记 |

## 2. 用 7 点位模拟 CSV 联调

联调库建好、账号配置完成后，可用脚本一次完成建表、登记设备、导入和校验。在本目录执行：

```powershell
$env:PGHOST = 'localhost'
$env:PGUSER = 'postgres'
$python = 'C:\Users\603-public\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
$psql = 'C:\Program Files\QGIS 3.30.3\bin\psql.exe'
.\start-demo.ps1 -Python $python -Psql $psql
```

脚本固定连接 `mosquito_monitoring_test`，使用 11 月 1–2 日的两天样例。后续按 [Demo 实施计划](./Demo实施计划.md) 接 API、地图和预警。下面也保留了手动联调步骤。

### 打开点位 7 日趋势

联调库导入成功后，在同一 PowerShell 会话运行：

```powershell
& $python '.\demo_server.py' --psql $psql
```

打开 [驾驶舱的点位 7 日趋势](http://127.0.0.1:8765/#point7-daily)。选择均值或中位数。导入增量后点击「刷新」即可重读数据库。接口为 `/api/demo/daily-stats?point=7&from=2026-11-01&to=2026-11-02`，演示服务仅监听本机。

演示机暂时没有 PostgreSQL 服务时，可用 `& $python '.\demo_server.py' --snapshot` 查看从联调数据库导出的同一批日统计。页面会标记「数据库快照」。追加数据的演示使用实时数据库模式。

### 打开小时趋势与地图

在相同 Demo 服务中打开 [小时趋势与地图](http://127.0.0.1:8765/?hourly=1#hourly-panel)。选择北京时间的日期、小时和点位。折线图显示当前点位的逐小时最大单条蚊数，地图高亮该点位，并用该小时全部点位的读数计算 2 米空间估算。3 号临时采用场地局部坐标 `(34, 23) 米`，页面标记为模拟坐标。现有 GIS 文件仍保留原始布点。

接口 `/api/demo/hourly-stats?date=2026-11-01` 查询 `app.point_hourly_stats`。已有联调库重跑 `start-demo.ps1` 后会为已导入数据补建小时汇总。实时模式下，导入新观测后点击「刷新」即可重查对应日期。

### 演示预警与居民提示

驾驶舱在小时趋势下显示「演示预警」，可切换当前小时或全天。单条六类蚊数合计 **大于 25** 时触发。同一点位同一小时只显示最大读数对应的一条记录，最大值并列时显示最早的记录。定位按钮会切换到对应小时并选中该点位。

[居民提示](http://127.0.0.1:8765/蚊媒监测平台-居民首页.html)与驾驶舱共用 `/api/demo/alerts?date=2026-11-01`，把同一点位相邻的触发小时合并成时段。所有预警来自 `app.demo_alerts` 的模拟数据。已有数据库重跑 `start-demo.ps1` 可补齐预警所需的记录时间。若使用独立图表账号，需补授下方 `app.demo_alerts` 的读取权限。

现场联调可给 11 月 1 日 09:00 时段的点位 7 追加一条时间不同的模拟记录。读数 25 不触发，26 触发。再次导入相同记录，仍只显示一条。此联调使用实时数据库模式。

在联调库执行：

```sql
INSERT INTO core.device (source_id, machine_no, point_no, is_demo)
SELECT 'demo', 'P' || lpad(n::text, 2, '0') || '-DEMO', n, true
FROM generate_series(1, 7) AS points(n)
ON CONFLICT (source_id, machine_no) DO NOTHING;
```

设置联调数据库连接。首次入库前，为当前运行账号配置 PostgreSQL 密码文件。联调结束后关闭此 PowerShell 会话。

```powershell
$env:PGHOST = 'localhost'
$env:PGDATABASE = 'mosquito_monitoring_test'
$env:PGUSER = 'postgres'
$python = 'C:\Users\603-public\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
$demoCsv = '..\outputs\01a1cf8a-45e7-7251-bbc4-2a43c03d864a\珠江未来小屋-7个点位模拟原始数据.csv'

& $python '.\scan.py' --file $demoCsv --source-id demo --check
& $python '.\scan.py' --file $demoCsv --source-id demo --psql $psql
& $python '.\scan.py' --file $demoCsv --source-id demo --psql $psql
```

首次应新增 336 条观测。原文件复扫应返回同一批次号、`inserted=0`。每个点位每天的六类合计指标应有 24 条有效样本。

```sql
SELECT point_no, observation_day, valid_count,
       round(mean_value, 2) AS mean_per_record, median_value, sum_value
FROM app.point_daily_stats
WHERE source_id = 'demo' AND is_demo AND metric = 'mosquito_total'
ORDER BY point_no, observation_day;
```

## 3. 接入厂商云端

1. 确认厂商有可由程序调用的 HTTPS CSV 导出地址，以及鉴权、数据保留和导出上限。定时拉取应使用固定地址或官方提供的长期调用方式。每次取可覆盖两次运行间隔及晚到数据的重叠窗口，例如最近 24 小时。停机超过窗口时，通过厂商历史导出补扫。
2. 将 `cloud.example.json` 复制为服务账号可读的私有配置文件，例如 `cloud.local.json`。填写真实 `csv_url`、`source_id`、`pg_service`、`psql` 路径。Bearer 令牌可通过运行账号的 `MOSQUITO_CSV_TOKEN` 环境变量提供，或写入受访问权限保护的私有配置键 `bearer_token`。云端入口目前接收 HTTP 200 返回的完整 CSV，兼容 GBK 和 UTF-8，单次上限 4 MiB。
3. 为任务运行账号配置 PostgreSQL 连接服务 `mosquito_ingest` 和密码文件。Windows 下可将服务配置放在 `%APPDATA%\postgresql\.pg_service.conf`，密码放在 `%APPDATA%\postgresql\pgpass.conf`。命令行和项目文件只使用服务名。远程数据库按部署环境配置 TLS。 [PostgreSQL 连接环境变量](https://www.postgresql.org/docs/current/libpq-envars.html)
4. 一次性登记真实设备的机器编号与点位。生产环境填厂商实际配置的编号和点位。

数据库连接服务配置示例：

```ini
[mosquito_ingest]
host=localhost
port=5432
dbname=mosquito_monitoring
user=mosquito_ingest
```

对应的密码文件条目为 `localhost:5432:mosquito_monitoring:mosquito_ingest:数据库密码`。服务配置、密码条目和实际数据库的名称应一致。

```sql
INSERT INTO core.device (source_id, machine_no, point_no, is_demo)
VALUES ('vendor01', '实际机器编号', 7, false);
```

服务账号需要 `raw`、`core`、`analysis` 的 schema 使用权限、入库函数执行权限和相关表权限。下面假定管理员已创建 `mosquito_ingest` 和 `mosquito_chart` 两个数据库角色，`mosquito_ingest` 的设备映射权限为只读。

```sql
GRANT USAGE ON SCHEMA raw, core, analysis TO mosquito_ingest;
GRANT EXECUTE ON FUNCTION raw.ingest_snapshot(jsonb) TO mosquito_ingest;
GRANT SELECT, INSERT ON raw.import_batch TO mosquito_ingest;
GRANT SELECT, INSERT, UPDATE ON raw.observation_import TO mosquito_ingest;
GRANT SELECT ON core.device TO mosquito_ingest;
GRANT SELECT, INSERT ON core.observation TO mosquito_ingest;
GRANT SELECT, INSERT, UPDATE ON analysis.observation_stats TO mosquito_ingest;
GRANT SELECT, INSERT, UPDATE ON analysis.hourly_stats TO mosquito_ingest;
GRANT EXECUTE ON FUNCTION analysis.refresh_hourly_stats(bigint) TO mosquito_ingest;
GRANT USAGE ON SCHEMA app TO mosquito_chart;
GRANT SELECT ON app.point_daily_stats, app.point_hourly_stats, app.demo_alerts TO mosquito_chart;
```

先试运行解析，再试运行入库：

```powershell
& $python '.\scan.py' --config '.\cloud.local.json' --check
& $python '.\scan.py' --config '.\cloud.local.json' --log-file '.\logs\scan.log'
```

## 4. 设置每小时扫描

如果厂商按整点完成上报和导出，先设为**每小时第 10 分钟**拉取，随后按厂商实际延迟调整。若有接近实时的要求，可在厂商允许的频率下缩短到 5–10 分钟。每轮只执行一次拉取，长期运行交给任务计划程序。 [Windows 任务计划命令](https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/schtasks-create)

在 Windows「任务计划程序 → 创建任务」中配置：

| 项目 | 设置 |
|---|---|
| 运行账号 | 有云端及数据库访问权限的后台账号，选择「不管用户是否登录都要运行」 |
| 触发器 | 从下一次 `HH:10` 启动，重复间隔 1 小时，持续时间无限期 |
| 程序 | 部署机上 `python.exe` 的绝对路径 |
| 参数 | `"D:\mosquito\scan.py" --config "D:\mosquito\cloud.local.json" --log-file "D:\mosquito\logs\scan.log"` |
| 起始于 | 代码目录，例如 `D:\mosquito` |
| 错过与并行 | 错过计划后尽快运行，已有实例时不启动新实例，最长运行时间可设 10 分钟 |

把示例路径替换成部署目录。保存后先右键「运行」，查看日志中的批次号，再用上面的应用层 SQL 查新日期。部署机须保持在线。

## 5. 重复上报、异常与图表

- **重复文件**按来源和 SHA-256 识别。**重叠窗口**按内部设备 ID 和完整记录时间去重。同键同读数记为重复，同键不同读数记为冲突，保留原标准记录供人工核对。
- 未登记的机器记录在原始层标记为 `unmapped`。补好映射后重扫包含该记录的 CSV 即可。云端窗口已过时，可用归档 CSV 重新执行 `--file`，并传入原 `--source-id`。
- 六类计数为非负整数。空值记缺测，真实零值参与计算。六项齐全才计算 `mosquito_total`。程序暂按原记录值统计，待厂商确认是分段计数还是累计计数后再定义日捕获量。采样计划确定后，才能计算整条应报未报。
- 本轮新增观测与汇总在同一数据库事务中提交。写入失败会回滚。下载遇到短暂故障会重试，长期故障由日志和任务计划结果排查。返回码 `0` 表示成功，`1` 表示执行或格式校验失败，`2` 表示已处理可入库数据但存在异常行。无法解析表头或 CSV 结构时记录任务错误，由厂商端保留原文件供重试。
- 驾驶舱后端查询 `app.point_daily_stats`。`mean_value` 和 `median_value` 对应图表切换，`valid_count` 用于提示样本数。正式页面过滤 `is_demo = false`。

```sql
SELECT line_no, status, reason, cells ->> 1 AS machine_no
FROM raw.observation_import
WHERE batch_id = 1 AND status IN ('invalid', 'unmapped', 'conflict')
ORDER BY line_no;
```
