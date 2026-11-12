// The GIS source retains its original survey points. Both demo pages use
// this layout to match the device IDs in the uploaded seven-point fixture.
(() => {
  const source = globalThis.siteGridData;
  if (!source) return;
  const [reference] = source.devices;
  const point3 = {
    id: 3, x: 34, y: 23, area: '模拟坐标', count: null,
    sampleCaptures: reference.sampleCaptures.map(() => null),
  };
  const devices = source.devices.filter(device => device.id >= 1 && device.id <= 7 && device.id !== 3);
  globalThis.demoSiteGridData = { ...source, devices: [...devices, point3].sort((a, b) => a.id - b.id) };
})();
