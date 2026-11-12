(() => {
  const byId = id => document.getElementById(id);
  const formatHour = hour => `${String(hour).padStart(2, '0')}:00`;

  function alertFeed({ clear, update }) {
    let request;
    return {
      async load(day) {
        request?.abort();
        request = new AbortController();
        const { signal } = request;
        clear('正在加载演示预警。');
        if (location.protocol === 'file:') {
          clear('请通过本地 Demo 服务打开页面。');
          return;
        }
        try {
          const response = await fetch(`/api/demo/alerts?date=${day}`, { signal });
          if (!response.ok) throw new Error('Demo alerts unavailable');
          const payload = await response.json();
          if (!signal.aborted) update(payload);
        } catch {
          if (!signal.aborted) clear('暂时无法读取预警，请刷新后重试。');
        }
      },
      stop() { request?.abort(); },
    };
  }

  function mapLink(day, hour, point) {
    const query = new URLSearchParams({ hourly: '1', date: day, hour: String(hour), device: String(point) });
    return `./蚊媒监测驾驶舱.html?${query}#plot`;
  }

  function initDashboardAlerts(onLocate) {
    const list = byId('demo-alerts-list');
    const status = byId('demo-alerts-status');
    const filter = byId('demo-alerts-scope');
    const residentLink = byId('demo-resident-link');
    let data;
    let hour;

    function render() {
      if (!data) return;
      const all = filter.value === 'day';
      const alerts = all ? data.alerts : data.alerts.filter(alert => alert.hour === hour);
      const period = all ? '全天' : `${formatHour(hour)} 时段`;
      status.textContent = `${period} · ${alerts.length ? `${alerts.length} 条演示预警` : '暂无演示预警记录'}`;
      list.replaceChildren();
      for (const alert of alerts) {
        const item = document.createElement('li');
        const trigger = document.createElement('button');
        trigger.type = 'button';
        trigger.className = 'map-action';
        trigger.textContent = `定位 ${alert.point} 号`;
        trigger.setAttribute('aria-label', `定位 ${alert.point} 号点位，${alert.observed_at.slice(11)}`);
        trigger.addEventListener('click', () => onLocate(alert));
        const details = document.createElement('span');
        const time = document.createElement('time');
        time.dateTime = `${alert.observed_at.replace(' ', 'T')}:00+08:00`;
        time.textContent = alert.observed_at.slice(11);
        details.append(time, ` · ${alert.reading} 只/条 > ${alert.threshold}`);
        item.append(details, trigger);
        list.append(item);
      }
    }

    filter.addEventListener('change', render);
    const feed = alertFeed({
      clear(message) {
        data = null;
        status.textContent = message;
        list.replaceChildren();
      },
      update(payload) {
        data = payload;
        render();
      },
    });
    return {
      load(day, selectedHour) {
        hour = selectedHour;
        residentLink.href = `./蚊媒监测平台-居民首页.html?date=${day}`;
        return feed.load(day);
      },
      selectHour(value) { hour = value; render(); },
      stop: feed.stop,
    };
  }

  function groupHours(alerts) {
    const byPoint = new Map();
    for (const { point, hour } of alerts) {
      if (!byPoint.has(point)) byPoint.set(point, new Set());
      byPoint.get(point).add(hour);
    }
    return [...byPoint].sort(([a], [b]) => a - b).map(([point, hours]) => {
      const periods = [];
      for (const hour of [...hours].sort((a, b) => a - b)) {
        const last = periods.at(-1);
        if (last && last.end === hour) last.end += 1;
        else periods.push({ start: hour, end: hour + 1 });
      }
      return { point, periods };
    });
  }

  function initResidentAlerts() {
    const date = byId('resident-date');
    const status = byId('resident-alert-status');
    const list = byId('resident-alerts');
    const overview = byId('resident-map-link');
    const requested = new URLSearchParams(location.search).get('date');
    if ([...date.options].some(option => option.value === requested)) date.value = requested;
    const feed = alertFeed({
      clear(message) {
        status.textContent = message;
        list.replaceChildren();
      },
      update(payload) {
        const source = payload.mode === 'snapshot' ? '数据库快照' : '联调数据库';
        const groups = groupHours(payload.alerts);
        status.textContent = `${payload.date} · 北京时间 · ${source}${groups.length ? '' : ' · 暂无演示预警记录'}`;
        list.replaceChildren();
        for (const { point, periods } of groups) {
          const area = globalThis.demoSiteGridData?.devices.find(device => device.id === point)?.area;
          const item = document.createElement('li');
          const name = document.createElement('a');
          name.href = mapLink(payload.date, periods[0].start, point);
          name.textContent = area ? `${point} 号点位 · ${area}` : `${point} 号点位`;
          const times = document.createElement('p');
          times.textContent = periods.map(({ start, end }) =>
            `${formatHour(start)}–${end === 24 ? '次日 00:00' : formatHour(end)}`).join('、');
          item.append(name, times);
          list.append(item);
        }
      },
    });
    function load() {
      overview.href = mapLink(date.value, 14, 7);
      feed.load(date.value);
    }
    date.addEventListener('change', load);
    byId('resident-refresh').addEventListener('click', load);
    load();
  }

  globalThis.initDashboardAlerts = initDashboardAlerts;
  if (byId('resident-alerts')) initResidentAlerts();
})();
