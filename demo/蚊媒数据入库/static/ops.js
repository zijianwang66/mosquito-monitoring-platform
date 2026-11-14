"use strict";

const el = id => document.getElementById(id);
const statusLabels = {running: "运行中", succeeded: "成功", failed: "失败", passed: "通过", error: "执行错误"};
const taskLabels = {
  archive_or_reuse_snapshot: "归档或复用文件",
  map_and_insert_observations: "映射并写入观测",
  refresh_daily_and_hourly_stats: "刷新日、小时汇总",
};
const ruleLabels = {
  IMPORT_INVALID: "无效行", IMPORT_UNMAPPED: "未映射设备", IMPORT_CONFLICT: "观测冲突",
  IMPORT_TRACE: "原始行追踪", MISSING_METRIC_VALUES: "指标缺测",
  DAILY_RECONCILIATION: "日汇总对账", HOURLY_RECONCILIATION: "小时汇总对账",
  QUALITY_EXECUTION: "检查执行",
};
const formatter = new Intl.DateTimeFormat("zh-CN", {
  timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit",
  hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23",
});
let cursor = null;
let selected = null;
let listRequest;
let detailRequest;

function node(tag, text) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  return element;
}

function valueText(value) {
  if (value == null) return "—";
  return typeof value === "object" ? JSON.stringify(value) : String(value);
}

function timeText(value) {
  const date = new Date(value);
  return value && Number.isFinite(date.getTime()) ? formatter.format(date) : "—";
}

function statusText(value) {
  const label = node("span", Object.hasOwn(statusLabels, value) ? statusLabels[value] : valueText(value));
  label.className = "status";
  if (Object.hasOwn(statusLabels, value)) label.classList.add(value);
  return label;
}

function addRow(body, values) {
  const row = node("tr");
  for (const value of values) {
    const cell = node("td");
    cell.append(value instanceof Node ? value : valueText(value));
    row.append(cell);
  }
  body.append(row);
  return row;
}

async function read(path, signal) {
  const response = await fetch(path, {signal});
  if (!response.ok) throw new Error(String(response.status));
  return response.json();
}

function highlight() {
  for (const button of el("runs").querySelectorAll("button")) {
    const active = button.dataset.runId === selected;
    button.setAttribute("aria-pressed", String(active));
    button.closest("tr").classList.toggle("selected", active);
  }
}

async function loadRuns(append = false) {
  listRequest?.abort();
  const request = new AbortController();
  listRequest = request;
  const query = new URLSearchParams({limit: "25"});
  if (el("status").value) query.set("status", el("status").value);
  if (append && cursor) query.set("before", cursor);
  if (!append) {
    cursor = null;
    el("runs").replaceChildren();
    el("runs-table").hidden = true;
    el("more").hidden = true;
  }
  el("more").disabled = true;
  el("list-message").textContent = "正在加载运行记录…";
  try {
    const page = await read("/api/ops/runs?" + query, request.signal);
    if (request.signal.aborted) return;
    for (const run of page.items) {
      const button = node("button", run.run_id);
      button.type = "button";
      button.dataset.runId = run.run_id;
      button.setAttribute("aria-label", "查看运行 " + run.run_id);
      button.addEventListener("click", () => loadDetail(run.run_id));
      addRow(el("runs"), [button, run.batch_id, statusText(run.status), timeText(run.started_at)]);
    }
    highlight();
    cursor = page.next_before;
    el("runs-table").hidden = !el("runs").children.length;
    el("more").hidden = !cursor;
    el("list-message").textContent = el("runs-table").hidden ? "没有符合条件的运行记录。" : "时间为北京时间。";
  } catch {
    if (!request.signal.aborted) el("list-message").textContent = "运行记录暂时无法读取，请检查数据库连接后重试。";
  } finally {
    if (listRequest === request) el("more").disabled = false;
  }
}

function addTable(parent, title, columns, rows, empty) {
  parent.append(node("h3", title));
  if (!rows.length) {
    parent.append(node("p", empty));
    return;
  }
  const wrapper = node("div");
  wrapper.className = "scroll";
  const table = node("table");
  table.setAttribute("aria-label", title);
  const heading = table.createTHead().insertRow();
  for (const label of columns) {
    const th = node("th", label);
    th.scope = "col";
    heading.append(th);
  }
  const body = table.createTBody();
  for (const values of rows) addRow(body, values);
  wrapper.append(table);
  parent.append(wrapper);
}

async function loadDetail(runId) {
  detailRequest?.abort();
  const request = new AbortController();
  detailRequest = request;
  selected = runId;
  highlight();
  el("detail").hidden = true;
  el("detail-message").textContent = "正在读取运行 " + runId + "…";
  try {
    const run = await read("/api/ops/runs/" + encodeURIComponent(runId), request.signal);
    if (request.signal.aborted) return;
    const detail = el("detail");
    const title = node("p", "运行 " + run.run_id + " · 批次 " + valueText(run.batch_id) + " · ");
    title.append(statusText(run.status));
    detail.replaceChildren(title, node("p", "开始 " + timeText(run.started_at) + "　结束 " + timeText(run.finished_at)));
    if (run.failure_reason) detail.append(node("p", "失败原因 · " + run.failure_reason));
    addTable(detail, "分层任务", ["层级", "任务", "状态", "输入 / 输出", "影响时段或失败原因"], run.tasks.map(task => [
      task.layer, taskLabels[task.task_name] ?? task.task_name, statusText(task.status),
      valueText(task.input_rows) + " / " + valueText(task.output_rows),
      task.failure_reason || (task.affected_from ? timeText(task.affected_from) + " 至 " + timeText(task.affected_until) : null),
    ]), "该运行暂无任务记录。");
    addTable(detail, "质量检查", ["规则", "结果", "期望 / 实际", "检查时间"], run.quality_checks.map(check => [
      ruleLabels[check.rule_code] ?? check.rule_code, statusText(check.result),
      valueText(check.expected_value) + " / " + valueText(check.actual_value), timeText(check.checked_at),
    ]), "该运行暂无质量检查记录。");
    el("detail-message").textContent = "";
    detail.hidden = false;
  } catch (error) {
    if (!request.signal.aborted) el("detail-message").textContent = error.message === "404"
      ? "找不到该运行记录，请刷新运行列表。"
      : "运行详情暂时无法读取，请刷新后重试。";
  }
}

el("status").addEventListener("change", () => {
  detailRequest?.abort();
  selected = null;
  el("detail").hidden = true;
  el("detail-message").textContent = "选择一条运行记录查看详情。";
  loadRuns();
});
el("refresh").addEventListener("click", () => {
  loadRuns();
  if (selected) loadDetail(selected);
});
el("more").addEventListener("click", () => loadRuns(true));
loadRuns();
