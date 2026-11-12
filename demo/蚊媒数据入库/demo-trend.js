(() => {
  const status = document.getElementById('daily-status');
  const metric = document.getElementById('daily-metric');
  const refresh = document.getElementById('daily-refresh');
  const table = document.getElementById('daily-table');
  const body = document.getElementById('daily-rows');
  const column = document.getElementById('daily-value-label');
  const dates = ['2026-11-01', '2026-11-02'];
  const number = new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 2 });
  let result;

  function render() {
    const key = metric.value;
    column.textContent = `${metric.selectedOptions[0].textContent}（只/条）`;
    const byDate = new Map(result.rows.map(row => [row.day, row]));
    const maximum = Math.max(1, ...result.rows.map(row => row[key] ?? 0));
    body.replaceChildren();
    for (const date of dates) {
      const row = byDate.get(date);
      const tr = body.insertRow();
      const label = document.createElement('th');
      label.scope = 'row';
      label.textContent = date.slice(5);
      tr.append(label);
      const cell = tr.insertCell();
      const value = row?.[key];
      if (value == null) {
        cell.textContent = '缺测';
      } else {
        const valueCell = document.createElement('div');
        valueCell.className = 'daily-value';
        const track = document.createElement('span');
        track.className = 'daily-bar';
        track.setAttribute('aria-hidden', 'true');
        const fill = document.createElement('span');
        fill.style.width = `${100 * value / maximum}%`;
        track.append(fill);
        const text = document.createElement('span');
        text.textContent = number.format(value);
        valueCell.append(track, text);
        cell.append(valueCell);
      }
      tr.insertCell().textContent = row?.valid_count ?? '—';
    }
    table.hidden = false;
    const source = result.mode === 'snapshot' ? '数据库快照' : '联调数据库';
    const empty = result.rows.length === 0 ? ' · 此区间暂无观测' : '';
    status.textContent = `2026 年 11 月 1–2 日 · 六类蚊数合计 · 模拟数据 · ${source}${empty}`;
  }

  async function load() {
    if (location.protocol === 'file:') {
      status.textContent = '请通过本地 Demo 服务打开驾驶舱，读取点位日统计。';
      refresh.disabled = true;
      return;
    }
    refresh.disabled = true;
    status.textContent = '正在加载日统计。';
    try {
      const response = await fetch(`/api/demo/daily-stats?point=7&from=${dates[0]}&to=${dates[1]}`);
      if (!response.ok) throw new Error('Daily statistics unavailable');
      result = await response.json();
      render();
    } catch {
      table.hidden = true;
      result = null;
      status.textContent = '暂时无法读取日统计，请检查 Demo 服务和数据库后刷新。';
    } finally {
      refresh.disabled = false;
    }
  }

  metric.addEventListener('change', () => {
    if (result) render();
  });
  refresh.addEventListener('click', load);
  load();
})();
