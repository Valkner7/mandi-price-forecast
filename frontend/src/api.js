// Talks to the FastAPI endpoints defined in app.py (/meta, /predict,
// /history, /trends). In dev, Vite's server.proxy (see vite.config.js)
// forwards these to http://127.0.0.1:8000. In production the built
// frontend is expected to be served same-origin by the backend, so these
// relative paths keep working unchanged.

async function fetchJSON(url) {
  const res = await fetch(url);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(body.detail || `Request failed (${res.status})`);
    err.status = res.status;
    throw err;
  }
  return body;
}

export function getMeta() {
  return fetchJSON('/meta');
}

export function getPredict(crop, mandi) {
  return fetchJSON(`/predict?crop=${encodeURIComponent(crop)}&mandi=${encodeURIComponent(mandi)}`);
}

export function getHistory(crop, mandi, days = 45) {
  return fetchJSON(`/history?crop=${encodeURIComponent(crop)}&mandi=${encodeURIComponent(mandi)}&days=${days}`);
}

export function getTrends(crops) {
  const query = crops ? `?crops=${encodeURIComponent(crops)}` : '';
  return fetchJSON(`/trends${query}`);
}

export function getNearbyMandis(lat, lon, limit = 22, crop) {
  const params = new URLSearchParams({ lat, lon, limit });
  if (crop) params.set('crop', crop);
  return fetchJSON(`/api/nearby-mandis?${params.toString()}`);
}
