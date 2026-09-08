import { BrowserRouter, Routes, Route } from 'react-router-dom';
import DashboardPage from './pages/DashboardPage';
import NearbyPage from './pages/NearbyPage';

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<DashboardPage />} />
        <Route path="/nearby" element={<NearbyPage />} />
      </Routes>
    </BrowserRouter>
  );
}
