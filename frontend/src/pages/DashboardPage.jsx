import { useEffect, useState } from 'react';
import Sidebar from '../components/Sidebar';
import StatusBanner from '../components/StatusBanner';
import PageHeader from '../components/PageHeader';
import Hero from '../components/Hero';
import PriceChart from '../components/PriceChart';
import MoversPanel from '../components/MoversPanel';
import AlertsPanel from '../components/AlertsPanel';
import PriceTable from '../components/PriceTable';
import { getMeta, getPredict, getHistory, getTrends } from '../api';
import '../styles/dashboard.css';

const FALLBACK_RELIABLE_CROPS = ['Potato', 'Onion', 'Tomato'];

export default function DashboardPage() {
  // ---- meta (crops/mandis lists) ----
  const [meta, setMeta] = useState(null); // { crops, mandis, reliableCrops }
  const [crop, setCrop] = useState(null);
  const [mandi, setMandi] = useState(null);

  // ---- global status banner ----
  const [status, setStatusState] = useState({ message: '', isError: false });
  function setStatus(message, isError) {
    setStatusState({ message: message || '', isError: !!isError });
  }

  // ---- selected crop/mandi: hero + chart ----
  const [selectionLoading, setSelectionLoading] = useState(false);
  const [predict, setPredict] = useState(null);
  const [history, setHistory] = useState(null);
  const [chartError, setChartError] = useState('');

  // ---- market-wide trends: gainers/losers/alerts ----
  const [allTrendRows, setAllTrendRows] = useState([]);
  const [trendsError, setTrendsError] = useState('');

  // ---- selected crop across mandis: table ----
  const [tableRows, setTableRows] = useState([]);
  const [tableError, setTableError] = useState('');

  // ---- init: load meta, pick default crop/mandi ----
  useEffect(() => {
    (async () => {
      try {
        const m = await getMeta();
        const reliableCrops = m.reliable_crops || FALLBACK_RELIABLE_CROPS;
        setMeta({ crops: m.crops, mandis: m.mandis, reliableCrops });

        const defaultCrop = reliableCrops[0] || m.crops[0];
        const defaultMandi = m.mandis.includes('Rayya') ? 'Rayya' : m.mandis[0];
        setCrop(defaultCrop);
        setMandi(defaultMandi);
      } catch (err) {
        setStatus('Could not load crop/mandi list from the API: ' + err.message, true);
      }
    })();
  }, []);

  // ---- load predict + history whenever crop/mandi changes ----
  useEffect(() => {
    if (!crop || !mandi) return;
    let cancelled = false;
    (async () => {
      setSelectionLoading(true);
      setChartError('');
      setStatus(`Loading ${crop} at ${mandi}…`, false);
      try {
        const [p, h] = await Promise.all([getPredict(crop, mandi), getHistory(crop, mandi, 45)]);
        if (cancelled) return;
        setPredict(p);
        setHistory(h);
        setStatus(p.data_note || '', false);
        if (p.anomaly_flag?.latest_price_is_anomaly) {
          setStatus(
            `Heads up: the latest recorded price at ${p.mandi} was an unusually large day-over-day move — worth a second look before acting on it.`,
            false
          );
        }
      } catch (err) {
        if (cancelled) return;
        setPredict(null);
        setHistory(null);
        setChartError(err.message);
        setStatus(
          err.status === 422 ? err.message : `No data for ${crop} at ${mandi}: ${err.message}`,
          true
        );
      } finally {
        if (!cancelled) setSelectionLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [crop, mandi]);

  // ---- load market-wide trends once meta is ready (independent of selection) ----
  useEffect(() => {
    if (!meta) return;
    (async () => {
      try {
        const trends = await getTrends();
        const rows = [];
        for (const [c, mandiRows] of Object.entries(trends.crops)) {
          for (const r of mandiRows) rows.push({ crop: c, ...r });
        }
        setAllTrendRows(rows);
        setTrendsError('');
      } catch (err) {
        setAllTrendRows([]);
        setTrendsError(err.message);
      }
    })();
  }, [meta]);

  // ---- load crop-across-mandis table whenever crop changes ----
  useEffect(() => {
    if (!crop) return;
    let cancelled = false;
    (async () => {
      try {
        const trends = await getTrends(crop);
        const rows = trends.crops[crop] || [];
        if (cancelled) return;
        if (!rows.length) {
          setTableRows([]);
          setTableError('No mandis with enough history for this crop.');
        } else {
          setTableRows(rows);
          setTableError('');
        }
      } catch (err) {
        if (cancelled) return;
        setTableRows([]);
        setTableError(err.message);
      }
    })();
    return () => { cancelled = true; };
  }, [crop]);

  const gainers = [...allTrendRows].sort((a, b) => b.pct_change - a.pct_change).slice(0, 5);
  const losers = [...allTrendRows].sort((a, b) => a.pct_change - b.pct_change).slice(0, 5);

  return (
    <div className="app-shell">
      <Sidebar
        footer={
          <>
            Live data from this app&apos;s own <code>/predict</code>, <code>/history</code>{' '}
            and <code>/trends</code> endpoints. Reliable forecasts currently cover{' '}
            <strong>{(meta?.reliableCrops || FALLBACK_RELIABLE_CROPS).join(', ')}</strong> — other
            crops may return &quot;not enough history.&quot;
          </>
        }
      />

      <main className="main">
        <PageHeader
          crops={meta?.crops || []}
          mandis={meta?.mandis || []}
          reliableCrops={meta?.reliableCrops || []}
          crop={crop}
          mandi={mandi}
          onCropChange={setCrop}
          onMandiChange={setMandi}
          disabled={selectionLoading}
        />

        <StatusBanner message={status.message} isError={status.isError} />

        {predict && <Hero predict={predict} />}

        <div className="panel">
          <div className="panel-header">
            <div>
              <div className="panel-title">Price trend &amp; forecast</div>
              <div className="panel-sub">Recent actual price, joined to the 7-day forecast</div>
            </div>
            <div className="legend-row">
              <span className="legend-dot">
                <span className="legend-swatch" style={{ background: '#173A27' }}></span>Actual
              </span>
              <span className="legend-dot">
                <span className="legend-swatch" style={{ background: '#C89635' }}></span>Forecast
              </span>
            </div>
          </div>
          {predict && history ? (
            <PriceChart history={history} predict={predict} />
          ) : (
            <div className="empty-state">{chartError}</div>
          )}
        </div>

        <div className="two-col">
          <div className="panel">
            <div className="panel-header">
              <div>
                <div className="panel-title">Top gainers</div>
                <div className="panel-sub">Largest projected 7-day increases</div>
              </div>
            </div>
            <MoversPanel rows={trendsError ? [] : gainers} emptyMessage={trendsError || 'No movers to show yet.'} />
          </div>
          <div className="panel">
            <div className="panel-header">
              <div>
                <div className="panel-title">Top losers</div>
                <div className="panel-sub">Largest projected 7-day declines</div>
              </div>
            </div>
            <MoversPanel rows={trendsError ? [] : losers} emptyMessage={trendsError || 'No movers to show yet.'} />
          </div>
        </div>

        <div className="panel">
          <div className="panel-header">
            <div>
              <div className="panel-title">Alerts</div>
              <div className="panel-sub">Biggest projected moves across tracked markets</div>
            </div>
          </div>
          {trendsError ? (
            <div className="empty-state">{trendsError}</div>
          ) : (
            <AlertsPanel rows={allTrendRows} />
          )}
        </div>

        <div className="panel">
          <div className="panel-header">
            <div>
              <div className="panel-title">{crop ? `${crop} across mandis` : 'Across mandis'}</div>
              <div className="panel-sub">Today&apos;s price and forecast direction by market</div>
            </div>
          </div>
          <PriceTable rows={tableRows} emptyMessage={tableError ? `${crop}: ${tableError}` : undefined} />
        </div>
      </main>
    </div>
  );
}
