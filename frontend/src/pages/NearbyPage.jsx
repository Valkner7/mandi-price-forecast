import { useEffect, useState } from 'react';
import { MapContainer, TileLayer, Marker, Popup, useMap } from 'react-leaflet';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import Sidebar from '../components/Sidebar';
import StatusBanner from '../components/StatusBanner';
import { getMeta, getNearbyMandis } from '../api';
import { inr } from '../utils/format';
import '../styles/dashboard.css';
import '../styles/nearby.css';

// Leaflet's default marker icon paths break under bundlers like Vite
// because the CSS-relative image URLs it hardcodes don't resolve through
// the module graph. Point the default icon at the same CDN the old
// no-build dashboard used, which sidesteps the bundler path issue entirely.
delete L.Icon.Default.prototype._getIconUrl;
L.Icon.Default.mergeOptions({
  iconRetinaUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/images/marker-icon-2x.png',
  iconUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/images/marker-icon.png',
  shadowUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/images/marker-shadow.png',
});

const redIcon = L.icon({
  iconUrl: 'https://raw.githubusercontent.com/pointhi/leaflet-color-markers/master/img/marker-icon-2x-red.png',
  shadowUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/0.7.7/images/marker-shadow.png',
  iconSize: [25, 41],
  iconAnchor: [12, 41],
  popupAnchor: [1, -34],
  shadowSize: [41, 41],
});

// Punjab, centred near Ludhiana — same neutral reference point the old
// no-build page used before a GPS fix is available.
const LUDHIANA_CENTER = { lat: 30.901, lon: 75.8573 };

// Recenters the map imperatively when a GPS fix comes in (mirrors the old
// map.setView call inside drawUserMarker).
function RecenterOnFix({ fix }) {
  const map = useMap();
  useEffect(() => {
    if (fix) map.setView([fix.lat, fix.lon], 10);
  }, [fix, map]);
  return null;
}

export default function NearbyPage() {
  const [crops, setCrops] = useState([]);
  const [crop, setCrop] = useState('');
  const [mandis, setMandis] = useState([]);
  const [totalMandis, setTotalMandis] = useState(null);
  const [fix, setFix] = useState(null); // { lat, lon } once the user shares GPS
  const [locating, setLocating] = useState(false);
  const [statusLabel, setStatusLabel] = useState('Location not shared yet');
  const [status, setStatusState] = useState({ message: '', isError: false });

  function setStatus(message, isError) {
    setStatusState({ message: message || '', isError: !!isError });
  }

  // ---- init: load crop list for the price-overlay selector ----
  useEffect(() => {
    (async () => {
      try {
        const meta = await getMeta();
        const reliable = meta.reliable_crops || ['Potato', 'Onion', 'Tomato'];
        const ordered = [
          ...reliable.filter((c) => meta.crops.includes(c)),
          ...meta.crops.filter((c) => !reliable.includes(c)),
        ];
        setCrops(ordered);
        setCrop(reliable[0] || '');
      } catch (err) {
        setStatus('Could not load crop list: ' + err.message, true);
      }
    })();
  }, []);

  // ---- load mandis whenever crop or GPS fix changes ----
  // Before a GPS fix, show the closest mandis to Ludhiana's centre as a
  // neutral reference point (capped at `limit`, see mapSub/listSub labels
  // for the accurate "N of total" count) so the page isn't empty on first
  // load — same behaviour as the old loadAllMandisUnsorted, minus its
  // inaccurate "all mandis" claim.
  useEffect(() => {
    const center = fix || LUDHIANA_CENTER;
    let cancelled = false;
    (async () => {
      try {
        const data = await getNearbyMandis(center.lat, center.lon, 22, crop);
        if (cancelled) return;
        setMandis(data.mandis);
        if (typeof data.total_mandis === 'number') setTotalMandis(data.total_mandis);
      } catch (err) {
        if (cancelled) return;
        setStatus('Could not load nearby mandis: ' + err.message, true);
      }
    })();
    return () => { cancelled = true; };
  }, [crop, fix]);

  function locate() {
    if (!navigator.geolocation) {
      setStatus('Geolocation is not supported by this browser.', true);
      return;
    }
    setLocating(true);
    setStatusLabel('Detecting your location…');
    navigator.geolocation.getCurrentPosition(
      (position) => {
        const { latitude, longitude } = position.coords;
        setFix({ lat: latitude, lon: longitude });
        setStatusLabel(`Location: ${latitude.toFixed(3)}, ${longitude.toFixed(3)}`);
        setStatus(null);
        setLocating(false);
      },
      (error) => {
        setStatusLabel('Location not shared');
        setStatus(
          'Could not get your location — check browser permissions and try again. (' + error.message + ')',
          true
        );
        setLocating(false);
      },
      { enableHighAccuracy: true, timeout: 10000 }
    );
  }

  const listSub = fix
    ? crop
      ? `Sorted by distance from you — showing today's ${crop} price`
      : 'Sorted by distance from you'
    : "Share your location to see distances and today's price";

  const mapSub = fix
    ? `Your location (red pin) and the closest ${mandis.length} of ${totalMandis ?? '110+'} tracked mandis`
    : `Showing ${mandis.length} of ${totalMandis ?? '110+'} tracked mandis — tap "Use my location" to centre on you and sort by distance`;

  return (
    <div className="app-shell">
      <Sidebar
        footer={
          <>
            Uses your browser&apos;s GPS location and this app&apos;s own{' '}
            <code>/api/nearby-mandis</code> endpoint. Your location is sent to
            this server only to compute distance — it isn&apos;t stored.
          </>
        }
      />

      <main className="main">
        <div className="page-header">
          <div>
            <h1 className="page-title">MandiSameep</h1>
            <div className="page-subtitle">Nearby mandis</div>
            <div className="page-meta">{statusLabel}</div>
          </div>
          <div className="selector-row">
            <select
          id="crop-select-nearby"
          name="crop"
          className="selector"
          value={crop}
          onChange={(e) => setCrop(e.target.value)}
        >
              <option value="">No price overlay</option>
              {crops.map((c) => (
                <option key={c} value={c}>{c}</option>
              ))}
            </select>
            <button className="locate-btn" disabled={locating} onClick={locate}>
              Use my location
            </button>
          </div>
        </div>

        <StatusBanner message={status.message} isError={status.isError} />

        <div className="panel map-panel">
          <div className="panel-header">
            <div>
              <div className="panel-title">Map</div>
              <div className="panel-sub">{mapSub}</div>
            </div>
          </div>
          <div id="map">
            <MapContainer
              center={[30.9, 75.85]}
              zoom={8}
              scrollWheelZoom={false}
              style={{ height: '100%', width: '100%' }}
            >
              <TileLayer
                attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
                url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
                maxZoom={18}
              />
              {mandis.map((m) => (
                <Marker key={m.mandi} position={[m.latitude, m.longitude]}>
                  <Popup>
                    <b>{m.mandi}</b>
                    <br />
                    {m.district} district
                    {m.latest_price && (
                      <>
                        <br />
                        {inr(m.latest_price.price)} INR/quintal ({m.latest_price.date})
                      </>
                    )}
                  </Popup>
                </Marker>
              ))}
              {fix && (
                <Marker position={[fix.lat, fix.lon]} icon={redIcon}>
                  <Popup>
                    <b>Your location</b>
                  </Popup>
                </Marker>
              )}
              <RecenterOnFix fix={fix} />
            </MapContainer>
          </div>
        </div>

        <div className="panel">
          <div className="panel-header">
            <div>
              <div className="panel-title">Closest markets</div>
              <div className="panel-sub">{listSub}</div>
            </div>
          </div>
          {mandis.length ? (
            mandis.map((m, i) => (
              <div className="mandi-row" key={m.mandi}>
                <div className="mandi-row-rank">{i + 1}</div>
                <div className="mandi-row-main">
                  <div className="mandi-row-name">{m.mandi}</div>
                  <div className="mandi-row-sub">{m.district} district</div>
                </div>
                <div className="mandi-row-distance">{m.distance_km} km</div>
                {m.latest_price ? (
                  <div className="mandi-row-price">
                    {inr(m.latest_price.price)}
                    <span className="unit">INR/quintal, {m.latest_price.date}</span>
                  </div>
                ) : (
                  <div className="mandi-row-price mandi-row-sub">No price data</div>
                )}
              </div>
            ))
          ) : (
            <div className="empty-state">No mandi data to show yet.</div>
          )}
        </div>
      </main>
    </div>
  );
}
