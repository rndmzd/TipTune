import { useEffect, useRef } from 'react';
import { Navigate, Route, Routes } from 'react-router-dom';

import { DashboardPage } from './pages/DashboardPage';
import { SettingsPage } from './pages/SettingsPage';
import { SetupPage } from './pages/SetupPage';
import { HelpPage } from './pages/HelpPage';
import { EventsPage } from './pages/EventsPage';
import { HistoryPage } from './pages/HistoryPage';
import { StatsPage } from './pages/StatsPage';
import { apiJson } from './api';

function isTauriRuntime(): boolean {
  const w: any = window as any;
  return !!(w && (w.__TAURI_INTERNALS__ || w.__TAURI__));
}

function autoCheckUpdatesEnabled(cfg: Record<string, Record<string, string>>): boolean {
  const raw = ((cfg.General || {}).auto_check_updates || '').toString();
  const s = (raw || 'true').trim().toLowerCase();
  return !(s === 'false' || s === '0' || s === 'no');
}

export function App() {
  const didAutoCheckRef = useRef<boolean>(false);

  useEffect(() => {
    if (didAutoCheckRef.current) return;
    didAutoCheckRef.current = true;

    if (!isTauriRuntime()) return;

    const run = async () => {
      let backendReady = false;
      for (let i = 0; i < 10; i++) {
        try {
          await apiJson('/api/setup/status');
          backendReady = true;
          break;
        } catch {
          await new Promise((r) => window.setTimeout(r, 1000));
        }
      }

      if (!backendReady) return;

      let cfg: Record<string, Record<string, string>> | null = null;
      try {
        const resp = await apiJson<{ ok: true; config: Record<string, Record<string, string>> }>('/api/config');
        cfg = resp?.config || {};
      } catch {
        return;
      }

      if (!cfg) return;
      if (!autoCheckUpdatesEnabled(cfg)) return;

      try {
        const mod: any = await import('@tauri-apps/plugin-updater');
        const checkFn = mod?.check || mod?.checkUpdate;
        if (typeof checkFn !== 'function') return;

        const res: any = await checkFn();
        const update = res && typeof res === 'object' ? res : null;
        const available =
          (typeof update?.available === 'boolean' && update.available) ||
          typeof update?.downloadAndInstall === 'function' ||
          typeof update?.download === 'function';
        if (!update || !available) return;

        const versionLine = typeof update?.version === 'string' && update.version ? `\nVersion: ${update.version}` : '';
        const dateLine = typeof update?.date === 'string' && update.date ? `\nDate: ${update.date}` : '';

        if (!confirm(`Update available.${versionLine}${dateLine}\n\nDownload + install now?`)) return;

        if (typeof update.downloadAndInstall === 'function') {
          await update.downloadAndInstall();
          return;
        }

        if (typeof update.download === 'function') {
          await update.download();
        }
        if (typeof update.install === 'function') {
          await update.install();
          return;
        }
      } catch {
      }
    };

    run().catch(() => {});
  }, []);

  return (
    <Routes>
      <Route path="/" element={<DashboardPage />} />
      <Route path="/settings" element={<SettingsPage />} />
      <Route path="/setup" element={<SetupPage />} />
      <Route path="/help" element={<HelpPage />} />
      <Route path="/events" element={<EventsPage />} />
      <Route path="/history" element={<HistoryPage />} />
      <Route path="/stats" element={<StatsPage />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
