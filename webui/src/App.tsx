import { Navigate, Route, Routes } from 'react-router-dom';

import { DashboardPage } from './pages/DashboardPage';
import { SettingsPage } from './pages/SettingsPage';
import { SetupPage } from './pages/SetupPage';
import { EventsPage } from './pages/EventsPage';

export function App() {
  return (
    <Routes>
      <Route path="/" element={<DashboardPage />} />
      <Route path="/settings" element={<SettingsPage />} />
      <Route path="/setup" element={<SetupPage />} />
      <Route path="/events" element={<EventsPage />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
