// Nearby Mandis — GPS map page. Talks to this app's own /meta and
// /api/nearby-mandis endpoints, plus the browser's Geolocation API.

const $ = (id) => document.getElementById(id);

const cropSelect = $('crop-select');
const locateBtn = $('locate-btn');
const statusLabel = $('status-label');
const statusBanner = $('status-banner');
const listSub = $('list-sub');
const mapSub = $('map-sub');
const nearbyList = $('nearby-list');
const listEmpty = $('list-empty');

let map = null;
let userMarker = null;
let mandiMarkersGroup = null;
let state = { crop: null, lastFix: null };

function inr(n) {
  return new Intl.NumberFormat('en-IN').format(Math.round(n));
}

function setStatus(message, isError) {
  if (!message) { statusBanner.hidden = true; return; }
  statusBanner.hidden = false;
  statusBanner.textContent = message;
  statusBanner.className = 'status-banner' + (isError ? ' error' : '');
}

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

// ---------- Map ----------

function initMap() {
  map = L.map('map', { scrollWheelZoom: false }).setView([30.9, 75.85], 8); // Punjab, centred near Ludhiana
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    maxZoom: 18,
  }).addTo(map);
  mandiMarkersGroup = L.layerGroup().addTo(map);
}

function drawAllMandis(mandis) {
  mandiMarkersGroup.clearLayers();
  mandis.forEach((m) => {
    const marker = L.marker([m.latitude, m.longitude]);
    const priceLine = m.latest_price
      ? `<br>${inr(m.latest_price.price)} INR/quintal (${m.latest_price.date})`
      : '';
    marker.bindPopup(`<b>${m.mandi}</b><br>${m.district} district${priceLine}`);
    mandiMarkersGroup.addLayer(marker);
  });
}

function drawUserMarker(lat, lon) {
  if (userMarker) map.removeLayer(userMarker);
  const redIcon = L.icon({
    iconUrl: 'https://raw.githubusercontent.com/pointhi/leaflet-color-markers/master/img/marker-icon-2x-red.png',
    shadowUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/0.7.7/images/marker-shadow.png',
    iconSize: [25, 41], iconAnchor: [12, 41], popupAnchor: [1, -34], shadowSize: [41, 41],
  });
  userMarker = L.marker([lat, lon], { icon: redIcon }).addTo(map).bindPopup('<b>Your location</b>').openPopup();
  map.setView([lat, lon], 10);
}

// ---------- List ----------

function renderList(mandis) {
  nearbyList.innerHTML = '';
  listEmpty.hidden = mandis.length > 0;
  mandis.forEach((m, i) => {
    const row = document.createElement('div');
    row.className = 'mandi-row';
    const priceHtml = m.latest_price
      ? `<div class="mandi-row-price">${inr(m.latest_price.price)}<span class="unit">INR/quintal, ${m.latest_price.date}</span></div>`
      : `<div class="mandi-row-price mandi-row-sub">No price data</div>`;
    row.innerHTML = `
      <div class="mandi-row-rank">${i + 1}</div>
      <div class="mandi-row-main">
        <div class="mandi-row-name">${m.mandi}</div>
        <div class="mandi-row-sub">${m.district} district</div>
      </div>
      <div class="mandi-row-distance">${m.distance_km} km</div>
      ${priceHtml}
    `;
    nearbyList.appendChild(row);
  });
}

// ---------- Fetch + orchestrate ----------

async function loadNearby(lat, lon) {
  const crop = cropSelect.value;
  const url = `/api/nearby-mandis?lat=${lat}&lon=${lon}&limit=22${crop ? `&crop=${encodeURIComponent(crop)}` : ''}`;
  const data = await fetchJSON(url);
  drawAllMandis(data.mandis);
  drawUserMarker(lat, lon);
  renderList(data.mandis);
  listSub.textContent = crop ? `Sorted by distance from you \u2014 showing today's ${crop} price` : 'Sorted by distance from you';
  mapSub.textContent = 'Your location (red pin) and every tracked mandi, closest first';
}

async function loadAllMandisUnsorted() {
  // Before we have a GPS fix, still show every mandi on the map (distance
  // from Ludhiana's centre as a neutral reference point) so the page isn't
  // empty on first load.
  const crop = cropSelect.value;
  const url = `/api/nearby-mandis?lat=30.9010&lon=75.8573&limit=22${crop ? `&crop=${encodeURIComponent(crop)}` : ''}`;
  const data = await fetchJSON(url);
  drawAllMandis(data.mandis);
}

function locate() {
  if (!navigator.geolocation) {
    setStatus('Geolocation is not supported by this browser.', true);
    return;
  }
  locateBtn.disabled = true;
  statusLabel.textContent = 'Detecting your location\u2026';
  navigator.geolocation.getCurrentPosition(
    async (position) => {
      const { latitude, longitude } = position.coords;
      state.lastFix = { lat: latitude, lon: longitude };
      statusLabel.textContent = `Location: ${latitude.toFixed(3)}, ${longitude.toFixed(3)}`;
      setStatus(null);
      try {
        await loadNearby(latitude, longitude);
      } catch (err) {
        setStatus('Could not load nearby mandis: ' + err.message, true);
      }
      locateBtn.disabled = false;
    },
    (error) => {
      statusLabel.textContent = 'Location not shared';
      setStatus('Could not get your location \u2014 check browser permissions and try again. (' + error.message + ')', true);
      locateBtn.disabled = false;
    },
    { enableHighAccuracy: true, timeout: 10000 }
  );
}

function onCropChange() {
  if (state.lastFix) {
    loadNearby(state.lastFix.lat, state.lastFix.lon).catch((err) => setStatus(err.message, true));
  } else {
    loadAllMandisUnsorted().catch(() => {});
  }
}

// ---------- Boot ----------

async function init() {
  initMap();

  try {
    const meta = await fetchJSON('/meta');
    const reliable = meta.reliable_crops || ['Potato', 'Onion', 'Tomato'];
    const ordered = [...reliable.filter((c) => meta.crops.includes(c)), ...meta.crops.filter((c) => !reliable.includes(c))];
    cropSelect.innerHTML = '<option value="">No price overlay</option>';
    ordered.forEach((c) => {
      const opt = document.createElement('option');
      opt.value = c;
      opt.textContent = c;
      cropSelect.appendChild(opt);
    });
    cropSelect.value = reliable[0] || '';
  } catch (err) {
    setStatus('Could not load crop list: ' + err.message, true);
  }

  cropSelect.addEventListener('change', onCropChange);
  locateBtn.addEventListener('click', locate);

  await loadAllMandisUnsorted().catch(() => {});
}

$('nav-dashboard').addEventListener('click', () => { window.location.href = '/dashboard/'; });
$('nav-voice').addEventListener('click', () => { window.location.href = '/voice-test'; });
$('nav-trends').addEventListener('click', () => { window.location.href = '/trends-dashboard'; });
$('nav-docs').addEventListener('click', () => { window.location.href = '/docs'; });

init();
