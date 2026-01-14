import { useEffect, useMemo, useRef, useState } from 'react';
import { Navigate, Route, Routes, useLocation } from 'react-router-dom';

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
  const location = useLocation();

  const isTauri = isTauriRuntime();
  const [backendState, setBackendState] = useState<'connecting' | 'ready' | 'failed'>(isTauri ? 'connecting' : 'ready');
  const [backendRetryNonce, setBackendRetryNonce] = useState<number>(0);

  useEffect(() => {
    if (!isTauri) return;

    let cancelled = false;
    const run = async () => {
      setBackendState('connecting');

      const start = Date.now();
      const maxMs = 20_000;
      const pollMs = 500;
      while (!cancelled && Date.now() - start < maxMs) {
        try {
          await apiJson('/api/setup/status', undefined, 750);
          if (cancelled) return;
          setBackendState('ready');
          return;
        } catch {
          await new Promise((r) => window.setTimeout(r, pollMs));
        }
      }

      if (cancelled) return;
      setBackendState('failed');
    };

    run().catch(() => {
      if (!cancelled) setBackendState('failed');
    });

    return () => {
      cancelled = true;
    };
  }, [isTauri, backendRetryNonce]);

  useEffect(() => {
    if (didAutoCheckRef.current) return;
    didAutoCheckRef.current = true;

    if (!isTauri) return;
    if (backendState !== 'ready') return;

    const run = async () => {
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
  }, [isTauri, backendState]);

  function GatedRoute(props: { element: JSX.Element }) {
    const loc = useLocation();
    const [allowed, setAllowed] = useState<boolean | null>(null);

    const forceDashboard = useMemo(() => {
      try {
        const params = new URLSearchParams(loc.search || '');
        const v = (params.get('dashboard') || '').trim().toLowerCase();
        return v === '1' || v === 'true' || v === 'yes' || v === 'on';
      } catch {
        return false;
      }
    }, [loc.search]);

    useEffect(() => {
      let cancelled = false;

      if (forceDashboard) {
        setAllowed(true);
        return () => {
          cancelled = true;
        };
      }

      (async () => {
        try {
          const data = await apiJson<{ ok: true; setup_complete: boolean }>('/api/setup/status');
          if (cancelled) return;
          setAllowed(!!data.setup_complete);
        } catch {
          if (cancelled) return;
          setAllowed(true);
        }
      })();

      return () => {
        cancelled = true;
      };
    }, [loc.pathname, loc.search, forceDashboard]);

    if (allowed === null) return null;
    return allowed ? props.element : <Navigate to="/setup" replace />;
  }

  if (isTauri && backendState !== 'ready') {
    return (
      <div style={{ minHeight: '70vh', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <div className="card" style={{ maxWidth: 520, width: '100%' }}>
          <h2>TipTune</h2>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginTop: 10 }}>
            {backendState === 'connecting' ? <div className="spinner" aria-hidden="true" /> : null}
            <div className="muted">
              {backendState === 'connecting'
                ? 'Starting up…'
                : 'Unable to connect to the TipTune backend. Make sure TipTune is allowed through your firewall, then try again.'}
            </div>
          </div>
          {backendState === 'failed' ? (
            <div className="actions" style={{ marginTop: 14 }}>
              <button type="button" onClick={() => setBackendRetryNonce((n) => n + 1)}>
                Retry
              </button>
            </div>
          ) : null}
        </div>
      </div>
    );
  }

  return (
    <Routes>
      <Route path="/" element={<GatedRoute element={<DashboardPage />} />} />
      <Route path="/settings" element={<GatedRoute element={<SettingsPage />} />} />
      <Route path="/events" element={<GatedRoute element={<EventsPage />} />} />
      <Route path="/history" element={<GatedRoute element={<HistoryPage />} />} />
      <Route path="/stats" element={<GatedRoute element={<StatsPage />} />} />
      <Route path="/setup" element={<SetupPage />} />
      <Route path="/help" element={<HelpPage />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
