// Mandi Setu dashboard — plain JS, no build step. Talks to the FastAPI
// endpoints defined in app.py (/meta, /predict, /history, /trends), all
// served from this same origin, so no CORS wrangling is needed in
// production even though the API itself allows any origin.

const $ = (id) => document.getElementById(id);

const cropSelect = $('crop-select');
const mandiSelect = $('mandi-select');
const heroEl = $('hero');
const heroStatus = $('hero-status');
const chartEmpty = $('chart-empty');
const tableEmpty = $('table-empty');
const tableWrap = $('table-wrap');

let chart = null;
let state = { crop: null, mandi: null, mandis: [], crops: [], reliableCrops: [] };

function inr(n) {
  return new Intl.NumberFormat('en-IN').format(Math.round(n));
}

function setStatus(message, isError) {
  if (!message) {
    heroStatus.hidden = true;
    return;
  }
  heroStatus.hidden = false;
  heroStatus.textContent = message;
  heroStatus.className = 'status-banner' + (isError ? ' error' : '');
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

// ---------- Boot ----------

async function init() {
  $('today-label').textContent = new Date().toLocaleDateString('en-IN', {
    weekday: 'long', day: 'numeric', month: 'long',
  });

  try {
    const meta = await fetchJSON('/meta');
    state.crops = meta.crops;
    state.mandis = meta.mandis;
    state.reliableCrops = meta.reliable_crops || ['Potato', 'Onion', 'Tomato'];
    $('reliable-crops-note').textContent = state.reliableCrops.join(', ');

    populateSelect(cropSelect, state.crops, state.reliableCrops);
    populateSelect(mandiSelect, state.mandis, []);

    // Default to a reliable crop if available, and a mandi likely to have
    // deep history for it (Rayya is used as the example throughout the
    // README); fall back to first-in-list otherwise.
    state.crop = state.reliableCrops[0] || state.crops[0];
    state.mandi = state.mandis.includes('Rayya') ? 'Rayya' : state.mandis[0];
    cropSelect.value = state.crop;
    mandiSelect.value = state.mandi;
  } catch (err) {
    setStatus('Could not load crop/mandi list from the API: ' + err.message, true);
    return;
  }

  cropSelect.addEventListener('change', onSelectionChange);
  mandiSelect.addEventListener('change', onSelectionChange);

  await Promise.all([loadSelection(), loadTrendsPanels(), loadCropAcrossMandis()]);
}

function populateSelect(select, values, prioritized) {
  select.innerHTML = '';
  const ordered = [
    ...prioritized.filter((v) => values.includes(v)),
    ...values.filter((v) => !prioritized.includes(v)),
  ];
  for (const v of ordered) {
    const opt = document.createElement('option');
    opt.value = v;
    opt.textContent = v;
    select.appendChild(opt);
  }
}

function onSelectionChange() {
  state.crop = cropSelect.value;
  state.mandi = mandiSelect.value;
  loadSelection();
  loadCropAcrossMandis();
}

// ---------- Hero + chart for the selected crop/mandi ----------

async function loadSelection() {
  cropSelect.disabled = true;
  mandiSelect.disabled = true;
  heroEl.hidden = true;
  chartEmpty.hidden = true;
  setStatus('Loading ' + state.crop + ' at ' + state.mandi + '…', false);

  try {
    const [predict, hist] = await Promise.all([
      fetchJSON(`/predict?crop=${encodeURIComponent(state.crop)}&mandi=${encodeURIComponent(state.mandi)}`),
      fetchJSON(`/history?crop=${encodeURIComponent(state.crop)}&mandi=${encodeURIComponent(state.mandi)}&days=45`),
    ]);
    setStatus(predict.data_note || '', false);
    renderHero(predict);
    renderChart(hist, predict);
    heroEl.hidden = false;
  } catch (err) {
    heroEl.hidden = true;
    if (chart) { chart.destroy(); chart = null; }
    chartEmpty.hidden = false;
    chartEmpty.textContent = err.message;
    setStatus(err.status === 422
      ? err.message
      : `No data for ${state.crop} at ${state.mandi}: ${err.message}`, true);
  } finally {
    cropSelect.disabled = false;
    mandiSelect.disabled = false;
  }
}

function renderHero(p) {
  $('hero-price-label').textContent = `Current price · ${p.crop}`;
  $('hero-price').innerHTML = `₹${inr(p.latest_price)} <span class="unit" id="hero-unit">/${p.unit.replace('INR per ', '')}</span>`;
  $('hero-date').textContent = `${p.mandi} mandi, as of ${p.latest_date}`;

  const trendEl = $('hero-trend');
  trendEl.textContent = (p.trend === 'rising' ? '↑ ' : p.trend === 'falling' ? '↓ ' : '→ ') + p.trend;
  trendEl.className = 'hero-value ' + (p.trend === 'rising' ? 'up' : p.trend === 'falling' ? 'down' : 'stable');
  $('hero-model').textContent = `Model: ${p.model}`;

  const forecastLast = p.forecast[p.forecast.length - 1];
  const forecastUp = forecastLast.price >= p.latest_price;
  const pct = ((forecastLast.price - p.latest_price) / p.latest_price) * 100;
  $('hero-forecast').textContent = `₹${inr(forecastLast.price)}`;
  const deltaEl = $('hero-forecast-delta');
  deltaEl.innerHTML = `<span class="${forecastUp ? 'up' : 'down'}">${forecastUp ? '↑' : '↓'} ${Math.abs(pct).toFixed(1)}% by ${forecastLast.date}</span>`;
  $('hero-confidence').textContent = p.confidence?.note || '';

  if (p.anomaly_flag?.latest_price_is_anomaly) {
    setStatus(
      `Heads up: the latest recorded price at ${p.mandi} was an unusually large day-over-day move — worth a second look before acting on it.`,
      false
    );
  }
}

function shortDate(iso) {
  const d = new Date(iso + 'T00:00:00');
  return d.toLocaleDateString('en-IN', { day: 'numeric', month: 'short' });
}

function renderChart(hist, predict) {
  const ctx = document.getElementById('price-chart');

  // Category axis (plain date-string labels) rather than Chart.js's 'time'
  // scale, so this works with just chart.umd.min.js from a CDN — no
  // date-fns adapter needed, no build step required.
  const actualDates = hist.points.map((p) => p.date);
  const forecastDates = predict.forecast.map((f) => f.date);
  const labels = [...actualDates, ...forecastDates];

  const actualData = hist.points.map((p) => p.price);
  // Pad forecast series with nulls under the actual-only range, then stitch
  // in the last actual value so the two lines join with no visual gap.
  const forecastData = [
    ...new Array(actualDates.length - 1).fill(null),
    actualData[actualData.length - 1],
    ...predict.forecast.map((f) => f.price),
  ];

  if (chart) chart.destroy();
  chart = new Chart(ctx, {
    type: 'line',
    data: {
      labels,
      datasets: [
        {
          label: 'Actual',
          data: actualData,
          borderColor: '#a6a290',
          backgroundColor: 'transparent',
          borderWidth: 2,
          pointRadius: 0,
          tension: 0.25,
          spanGaps: true,
        },
        {
          label: 'Forecast',
          data: forecastData,
          borderColor: '#d4a017',
          backgroundColor: 'transparent',
          borderWidth: 2,
          borderDash: [5, 4],
          pointRadius: 0,
          tension: 0.25,
          spanGaps: true,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 600 },
      interaction: { mode: 'index', intersect: false },
      scales: {
        x: {
          grid: { color: '#292b20' },
          ticks: {
            color: '#6f6c5e',
            font: { family: 'IBM Plex Mono', size: 11 },
            callback: function (value) { return shortDate(labels[value]); },
            maxTicksLimit: 8,
            autoSkip: true,
          },
        },
        y: {
          grid: { color: '#292b20' },
          ticks: {
            color: '#6f6c5e',
            font: { family: 'IBM Plex Mono', size: 11 },
            callback: (v) => `₹${Math.round(v / 100) / 10}k`,
          },
        },
      },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: '#21231a',
          borderColor: '#34362a',
          borderWidth: 1,
          titleFont: { family: 'IBM Plex Sans', size: 11.5 },
          bodyFont: { family: 'IBM Plex Mono', size: 12.5 },
          callbacks: {
            title: (items) => shortDate(labels[items[0].dataIndex]),
            label: (item) => `${item.dataset.label} ₹${inr(item.parsed.y)}`,
          },
        },
      },
    },
  });
}

// ---------- Cross-market panels (gainers / losers / alerts) ----------
// Uses /trends with its default crop list (the crops with enough history
// to forecast reliably) so this loads once and stays fast regardless of
// which crop/mandi the user has selected above.

async function loadTrendsPanels() {
  try {
    const trends = await fetchJSON('/trends');
    const rows = [];
    for (const [crop, mandiRows] of Object.entries(trends.crops)) {
      for (const r of mandiRows) {
        rows.push({ crop, ...r });
      }
    }
    const gainers = [...rows].sort((a, b) => b.pct_change - a.pct_change).slice(0, 5);
    const losers = [...rows].sort((a, b) => a.pct_change - b.pct_change).slice(0, 5);

    renderMovers('gainers-list', gainers);
    renderMovers('losers-list', losers);
    renderAlerts(rows);
  } catch (err) {
    $('gainers-list').innerHTML = emptyRow(err.message);
    $('losers-list').innerHTML = emptyRow(err.message);
    $('alerts-list').innerHTML = emptyRow(err.message);
  }
}

function renderMovers(containerId, rows) {
  const el = $(containerId);
  if (!rows.length) { el.innerHTML = emptyRow('No movers to show yet.'); return; }
  el.innerHTML = rows.map((r) => {
    const up = r.pct_change >= 0;
    return `
      <div class="mover-row">
        <div class="mover-name">
          <span class="mover-commodity">${r.crop}</span>
          <span class="mover-mandi">${r.mandi}</span>
        </div>
        <div class="mover-figures">
          <span class="mover-price">₹${inr(r.latest_price)}</span>
          <span class="pct-pill ${up ? 'up' : 'down'}">${up ? '↑' : '↓'} ${Math.abs(r.pct_change).toFixed(1)}%</span>
        </div>
      </div>`;
  }).join('');
}

function renderAlerts(rows) {
  const el = $('alerts-list');
  const sorted = [...rows].sort((a, b) => Math.abs(b.pct_change) - Math.abs(a.pct_change)).slice(0, 5);
  if (!sorted.length) { el.innerHTML = emptyRow('No alerts right now.'); return; }
  el.innerHTML = sorted.map((r) => {
    const severity = Math.abs(r.pct_change) > 4 ? 'high' : 'medium';
    const text = r.pct_change >= 0
      ? `${r.crop} at ${r.mandi} projected up ${r.pct_change.toFixed(1)}% over the next ${r.forecast_horizon_days} days.`
      : `${r.crop} at ${r.mandi} projected down ${Math.abs(r.pct_change).toFixed(1)}% over the next ${r.forecast_horizon_days} days — watch before selling.`;
    return `<div class="alert-row"><span class="alert-dot ${severity}"></span><span class="alert-text">${text}</span></div>`;
  }).join('');
}

function emptyRow(message) {
  return `<div class="empty-state">${message}</div>`;
}

// ---------- Price table: selected crop across every viable mandi ----------

async function loadCropAcrossMandis() {
  $('table-title').textContent = `${state.crop} across mandis`;
  tableWrap.hidden = false;
  tableEmpty.hidden = true;
  $('price-table-body').innerHTML = '';

  try {
    const trends = await fetchJSON(`/trends?crops=${encodeURIComponent(state.crop)}`);
    const rows = trends.crops[state.crop] || [];
    if (!rows.length) throw new Error('No mandis with enough history for this crop.');
    $('price-table-body').innerHTML = rows.map((r) => {
      const up = r.pct_change >= 0;
      return `
        <tr>
          <td>${r.mandi}</td>
          <td class="num">₹${inr(r.latest_price)}</td>
          <td class="num">₹${inr(r.forecast_price)}</td>
          <td class="num ${up ? 'up' : 'down'}">${up ? '↑' : '↓'} ${Math.abs(r.pct_change).toFixed(1)}%</td>
        </tr>`;
    }).join('');
  } catch (err) {
    tableWrap.hidden = true;
    tableEmpty.hidden = false;
    tableEmpty.textContent = `${state.crop}: ${err.message}`;
  }
}

// ---------- Sidebar nav shortcuts ----------

$('nav-voice').addEventListener('click', () => { window.location.href = '/voice-test'; });
$('nav-trends').addEventListener('click', () => { window.location.href = '/trends-dashboard'; });
$('nav-docs').addEventListener('click', () => { window.location.href = '/docs'; });

init();
