globalThis.initDemoHourly = ({ points, applyReadings, locatePoint }) => {
  const panel = document.getElementById('hourly-panel');
  const date = document.getElementById('hourly-date');
  const hour = document.getElementById('hourly-hour');
  const refresh = document.getElementById('hourly-refresh');
  const status = document.getElementById('hourly-status');
  const chart = document.getElementById('hourly-chart');
  const context = chart.getContext('2d');
  const hours = Array.from({ length: 24 }, (_, index) => index);
  const formatHour = index => String(index).padStart(2, '0');
  const parameters = new URLSearchParams(location.search);
  let results = null;
  let request;
  let selectedPoint = points.find(point => String(point.id) === parameters.get('device'))?.id ?? 7;

  for (const index of hours) {
    hour.add(new Option(`${formatHour(index)}:00`, String(index)));
  }
  const initialDay = parameters.get('date');
  const initialHour = parameters.get('hour');
  if ([...date.options].some(option => option.value === initialDay)) date.value = initialDay;
  hour.value = hours.map(String).includes(initialHour) ? initialHour : '14';
  const alerts = globalThis.initDashboardAlerts(alert => {
    hour.value = String(alert.hour);
    render();
    locatePoint(alert.point, true);
  });

  const pointNav = document.getElementById('hourly-points');
  const pointButtons = new Map(points.map(point => {
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = `${point.id}号`;
    button.setAttribute('aria-label', `${point.id}号点位${point.area ? `，${point.area}` : ''}`);
    button.setAttribute('aria-controls', chart.id);
    button.addEventListener('click', () => locatePoint(point.id));
    pointNav.append(button);
    return [point.id, button];
  }));

  function draw() {
    const { width, height } = chart.getBoundingClientRect();
    if (!width || !height) return;
    const ratio = window.devicePixelRatio || 1;
    chart.width = Math.round(width * ratio);
    chart.height = Math.round(height * ratio);
    context.scale(ratio, ratio);
    const left = 24;
    const right = width - 11;
    const top = 8;
    const bottom = height - 18;
    const step = Math.max(10, Math.ceil(Math.max(0, ...(results?.rows ?? []).map(row => row.value ?? 0)) / 4));
    const x = index => left + index * (right - left) / 23;
    const y = value => bottom - (bottom - top) * value / (4 * step);
    const selected = Number(hour.value);
    context.font = '10px system-ui';
    context.textBaseline = 'middle';
    context.fillStyle = '#506254';
    context.textAlign = 'right';
    for (let level = 0; level <= 4; level++) {
      const value = level * step;
      context.fillText(value, left - 4, y(value));
      context.strokeStyle = '#d8e0d4';
      context.beginPath();
      context.moveTo(left, y(value));
      context.lineTo(right, y(value));
      context.stroke();
    }
    context.textAlign = 'center';
    for (const index of [0, 6, 12, 18, 23]) {
      context.fillText(formatHour(index), x(index), height - 6);
    }
    context.strokeStyle = '#516c61';
    context.setLineDash([3, 3]);
    context.beginPath();
    context.moveTo(x(selected), top);
    context.lineTo(x(selected), bottom);
    context.stroke();
    context.setLineDash([]);
    context.strokeStyle = context.fillStyle = '#317360';
    context.lineWidth = 1.7;
    const values = hours.map(index => results?.byHour.get(index)?.get(selectedPoint)?.value);
    context.beginPath();
    for (const [index, value] of values.entries()) {
      if (value == null) continue;
      if (values[index - 1] == null) context.moveTo(x(index), y(value));
      else context.lineTo(x(index), y(value));
    }
    context.stroke();
    for (const [index, value] of values.entries()) {
      if (value != null) {
        context.beginPath();
        context.arc(x(index), y(value), index === selected ? 3.5 : 1.5, 0, 2 * Math.PI);
        context.fill();
      }
    }
  }

  function renderPoint() {
    for (const [point, button] of pointButtons) {
      button.setAttribute('aria-pressed', String(point === selectedPoint));
    }
    chart.setAttribute('aria-label', `${selectedPoint} 号点位逐小时最大单条蚊数`);
    if (results) {
      const source = results.mode === 'snapshot' ? '数据库快照' : '联调数据库';
      const reading = results.byHour.get(Number(hour.value))?.get(selectedPoint)?.value;
      const summary = reading == null ? '缺测' : `${reading} 只/条`;
      status.textContent = `${date.value} ${formatHour(hour.value)}:00 · ${selectedPoint} 号 ${summary} · ${source}`;
    }
    draw();
  }

  function render() {
    const values = results?.byHour.get(Number(hour.value)) ?? new Map();
    applyReadings(new Map([...values].map(([point, reading]) => [point, reading.value])));
    alerts.selectHour(Number(hour.value));
    renderPoint();
  }

  async function load() {
    request?.abort();
    const current = new AbortController();
    request = current;
    results = null;
    render();
    alerts.load(date.value, Number(hour.value));
    if (location.protocol === 'file:') {
      status.textContent = '请通过本地 Demo 服务打开页面，读取小时统计。';
      return;
    }
    status.textContent = '正在加载小时统计。';
    refresh.disabled = true;
    try {
      const response = await fetch(`/api/demo/hourly-stats?date=${date.value}`, { signal: current.signal });
      if (!response.ok) throw new Error('Hourly statistics unavailable');
      const payload = await response.json();
      if (current.signal.aborted) return;
      const byHour = new Map();
      for (const row of payload.rows) {
        if (!byHour.has(row.hour)) byHour.set(row.hour, new Map());
        byHour.get(row.hour).set(row.point, row);
      }
      results = { ...payload, byHour };
      render();
    } catch {
      if (!current.signal.aborted) status.textContent = '暂时无法读取小时统计，请检查 Demo 服务和数据库后刷新。';
    } finally {
      if (!current.signal.aborted) refresh.disabled = false;
    }
  }

  date.addEventListener('change', load);
  hour.addEventListener('change', render);
  refresh.addEventListener('click', load);
  new ResizeObserver(draw).observe(chart);
  return {
    point: () => selectedPoint,
    selectPoint(id) {
      selectedPoint = id;
      renderPoint();
    },
    setActive(active) {
      panel.hidden = !active;
      if (active) load();
      else {
        request?.abort();
        alerts.stop();
      }
    },
  };
};
