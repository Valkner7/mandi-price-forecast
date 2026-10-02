import { lazy, Suspense } from 'react';
import { BrowserRouter, Routes, Route } from 'react-router-dom';
import DashboardPage from './pages/DashboardPage';

// The map page pulls in Leaflet, which the dashboard never uses, so it is
// loaded on demand instead of being shipped with the main bundle.
const NearbyPage = lazy(() => import('./pages/NearbyPage'));

export default function App() {
  return (
    <BrowserRouter>
      <Suspense fallback={<p style={{ padding: '1rem' }}>Loading…</p>}>
        <Routes>
          <Route path="/" element={<DashboardPage />} />
          <Route path="/nearby" element={<NearbyPage />} />
        </Routes>
      </Suspense>
    </BrowserRouter>
  );
}
