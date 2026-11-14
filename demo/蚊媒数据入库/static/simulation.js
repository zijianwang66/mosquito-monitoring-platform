"use strict";

(() => {
  const byId = id => document.getElementById(id);
  const picker = byId("simulation-point");
  const chart = byId("point-chart");
  const message = byId("simulation-message");
  const time = new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", hourCycle: "h23",
  });
  const columns = [
    ["mosquito_total", "蚊媒总数"], ["aedes_male", "雄伊蚊"], ["aedes_female", "雌伊蚊"],
    ["culex_male", "雄库蚊"], ["culex_female", "雌库蚊"], ["anopheles_male", "雄按蚊"],
    ["anopheles_female", "雌按蚊"], ["temperature_c", "温度 °C"],
    ["humidity_pct", "湿度 %"], ["wind_speed_mps", "风速 m/s"],
  ];
  let rows = [];
  let pending = false;
  picker.add(new Option("全部点位", ""));
  for (let point = 1; point <= 9; point++) picker.add(new Option(point + " 号点位", point));
  for (const label of ["观测时间", "点位", "批次", "原始行", ...columns.map(([, label]) => label)]) {
    const th = document.createElement("th");
    th.scope = "col";
    th.textContent = label;
    byId("simulation-head").append(th);
  }

  function visibleRows() {
    return rows.filter(row => !picker.value || String(row.point_no) === picker.value);
  }

  function draw() {
    const records = visibleRows();
    const width = Math.max(240, chart.clientWidth);
    const left = 38, right = width - 20, top = 16, bottom = 222;
    const colors = ["#145b49", "#2866a0", "#a84731", "#7053a2", "#8a6207", "#1c7381", "#a33d75", "#536a29", "#604732"];
    const legend = byId("point-legend");
    legend.replaceChildren();
    chart.setAttribute("viewBox", "0 0 " + width + " 260");
    chart.replaceChildren();
    const svg = (tag, attrs, text) => {
      const item = document.createElementNS("http://www.w3.org/2000/svg", tag);
      for (const [key, value] of Object.entries(attrs)) item.setAttribute(key, value);
      if (text !== undefined) item.textContent = text;
      chart.append(item);
      return item;
    };
    if (!records.length) {
      svg("text", {x: left, y: 40}, "暂无该点位的模拟观测");
      return;
    }
    const timestamps = records.map(row => Date.parse(row.observed_at));
    const start = Math.min(...timestamps);
    const end = Math.max(...timestamps);
    const max = Math.max(4, Math.ceil(Math.max(...records.map(row => row.measurements?.mosquito_total ?? 0)) / 4) * 4);
    const x = stamp => start === end ? (left + right) / 2 : left + (stamp - start) / (end - start) * (right - left);
    const y = value => bottom - value / max * (bottom - top);
    for (let i = 0; i <= 4; i++) {
      const value = max * i / 4;
      svg("line", {x1: left, x2: right, y1: y(value), y2: y(value), stroke: "#cbdad4"});
      svg("text", {x: left - 7, y: y(value) + 4, "text-anchor": "end"}, value);
    }
    const ticks = start === end ? [start] : width < 480 ? [start, end] : [start, (start + end) / 2, end];
    ticks.forEach((stamp, index) => svg("text", {
      x: x(stamp), y: 246, "text-anchor": ticks.length === 1 ? "middle" : index === 0 ? "start" : index === ticks.length - 1 ? "end" : "middle",
    }, time.format(stamp)));
    for (let point = 1; point <= 9; point++) {
      const series = records.filter(row => row.point_no === point).reverse();
      if (!series.some(row => row.measurements?.mosquito_total != null)) continue;
      let path = "";
      let previous = null;
      const color = colors[point - 1];
      const label = document.createElement("span");
      label.textContent = point + " 号";
      label.style.setProperty("--series", color);
      legend.append(label);
      for (const row of series) {
        const value = row.measurements?.mosquito_total;
        const stamp = Date.parse(row.observed_at);
        if (value == null) { previous = null; continue; }
        path += (previous !== null && stamp - previous === 600000 ? "L" : "M") + x(stamp) + "," + y(value);
        previous = stamp;
        const dot = svg("circle", {cx: x(stamp), cy: y(value), r: 3, fill: color});
        const title = document.createElementNS(dot.namespaceURI, "title");
        title.textContent = point + " 号 · " + time.format(stamp) + " · " + value + " 只";
        dot.append(title);
      }
      svg("path", {d: path, stroke: color, "stroke-width": 2, fill: "none", "pointer-events": "none"});
    }
  }

  function render() {
    const body = byId("simulation-rows");
    body.replaceChildren();
    for (const row of visibleRows()) {
      const tr = body.insertRow();
      for (const value of [time.format(Date.parse(row.observed_at)), row.point_no, row.batch_id, row.line_no,
        ...columns.map(([key]) => row.measurements?.[key])]) tr.insertCell().textContent = value ?? "—";
    }
    draw();
  }

  async function refresh() {
    if (pending) return;
    pending = true;
    try {
      const response = await fetch("/api/ops/simulation", {signal: AbortSignal.timeout(15000)});
      if (!response.ok) throw new Error();
      rows = (await response.json()).items;
      render();
      message.textContent = rows.length
        ? "最新观测 " + time.format(Date.parse(rows[0].observed_at)) + "（北京时间） · 每 30 秒刷新"
        : "等待模拟脚本提交首批观测。";
    } catch {
      message.textContent = "读取观测失败，可点击刷新观测重试。";
    } finally {
      pending = false;
    }
  }

  picker.addEventListener("change", render);
  byId("simulation-refresh").addEventListener("click", refresh);
  new ResizeObserver(draw).observe(chart);
  setInterval(refresh, 30000);
  refresh();
})();
