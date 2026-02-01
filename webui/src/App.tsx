import { useEffect, useMemo, useRef, useState } from 'react';
import { Navigate, Route, Routes, useLocation } from 'react-router-dom';

import { DashboardPage } from './pages/DashboardPage';
import { SettingsPage } from './pages/SettingsPage';
import { SetupPage } from './pages/SetupPage';
import { HelpPage } from './pages/HelpPage';
import { EventsPage } from './pages/EventsPage';
import { HistoryPage } from './pages/HistoryPage';
import { StatsPage } from './pages/StatsPage';
import { apiJson, sseUrl } from './api';
import { PlaybackProvider } from './components/PlaybackContext';

type RequestConfig = {
  songCost: number | null;
  multiRequestTips: boolean;
};

type TipToast = {
  id: number;
  username: string;
  message: string;
  tokens: number;
};

function isTauriRuntime(): boolean {
  const w: any = window as any;
  return !!(w && (w.__TAURI_INTERNALS__ || w.__TAURI__));
}

function autoCheckUpdatesEnabled(cfg: Record<string, Record<string, string>>): boolean {
  const raw = ((cfg.General || {}).auto_check_updates || '').toString();
  const s = (raw || 'true').trim().toLowerCase();
  return !(s === 'false' || s === '0' || s === 'no');
}

function safeParseJSON(s: string): any {
  try {
    return JSON.parse(s);
  } catch {
    return null;
  }
}

function get(obj: any, path: string[], fallback: any) {
  try {
    let cur = obj;
    for (const key of path) {
      if (!cur || typeof cur !== 'object' || !(key in cur)) return fallback;
      cur = cur[key];
    }
    return cur == null ? fallback : cur;
  } catch {
    return fallback;
  }
}

function parseBool(raw: unknown, fallback: boolean): boolean {
  const s = String(raw ?? '').trim().toLowerCase();
  if (!s) return fallback;
  return !(s === 'false' || s === '0' || s === 'no' || s === 'off');
}

function parseSongCost(raw: unknown): number | null {
  const n = Number.parseInt(String(raw ?? ''), 10);
  return Number.isFinite(n) && n > 0 ? n : null;
}

function isSongRequestTip(tokens: number | null, songCost: number | null, multiRequestTips: boolean): boolean {
  if (tokens == null || !Number.isFinite(tokens) || tokens <= 0) return false;
  if (songCost == null || !Number.isFinite(songCost) || songCost <= 0) return false;
  return multiRequestTips ? tokens % songCost === 0 : tokens === songCost;
}

export function App() {
  const didAutoCheckRef = useRef<boolean>(false);
  const toastIdRef = useRef<number>(0);
  const toastTimersRef = useRef<Map<number, number>>(new Map());
  const requestConfigRef = useRef<RequestConfig>({ songCost: 27, multiRequestTips: true });

  const isTauri = isTauriRuntime();
  const [backendState, setBackendState] = useState<'connecting' | 'ready' | 'failed'>(isTauri ? 'connecting' : 'ready');
  const [backendRetryNonce, setBackendRetryNonce] = useState<number>(0);
  const [requestConfig, setRequestConfig] = useState<RequestConfig>({ songCost: 27, multiRequestTips: true });
  const [tipToasts, setTipToasts] = useState<TipToast[]>([]);

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

  const backendReady = !isTauri || backendState === 'ready';

  useEffect(() => {
    requestConfigRef.current = requestConfig;
  }, [requestConfig]);

  useEffect(() => {
    if (!backendReady) return;
    let cancelled = false;

    const loadConfig = async () => {
      try {
        const resp = await apiJson<{ ok: true; config: Record<string, Record<string, string>> }>('/api/config');
        if (cancelled) return;
        const general = resp?.config?.General || {};
        const parsedCost = parseSongCost((general as any).song_cost);
        setRequestConfig({
          songCost: parsedCost ?? 27,
          multiRequestTips: parseBool((general as any).multi_request_tips, true),
        });
      } catch {
      }
    };

    loadConfig().catch(() => {});
    const t = window.setInterval(() => {
      loadConfig().catch(() => {});
    }, 30000);

    return () => {
      cancelled = true;
      window.clearInterval(t);
    };
  }, [backendReady]);

  useEffect(() => {
    return () => {
      for (const timer of toastTimersRef.current.values()) {
        window.clearTimeout(timer);
      }
      toastTimersRef.current.clear();
    };
  }, []);

  useEffect(() => {
    if (!backendReady) return;

    const es = new EventSource(sseUrl('/api/events/sse'));
    es.onmessage = (e) => {
      const parsed = safeParseJSON(e.data);
      const ev = parsed && typeof parsed === 'object' ? (parsed as any).event || parsed : null;
      if (!ev || typeof ev !== 'object') return;
      const method = get(ev, ['method'], null);
      if (method !== 'tip') return;

      const tokensRaw = get(ev, ['object', 'tip', 'tokens'], null);
      const tokens =
        typeof tokensRaw === 'number' ? tokensRaw : Number.isFinite(Number(tokensRaw)) ? Number(tokensRaw) : null;

      const { songCost, multiRequestTips } = requestConfigRef.current;
      if (!isSongRequestTip(tokens, songCost, multiRequestTips)) return;

      const isAnon = get(ev, ['object', 'tip', 'isAnon'], false);
      const userFromUserObj = get(ev, ['object', 'user', 'username'], null);
      const userFromMessage = get(ev, ['object', 'message', 'fromUser'], null);
      const username = isAnon ? 'Anonymous' : userFromUserObj || userFromMessage || 'Unknown';

      const tipMessage = get(ev, ['object', 'tip', 'message'], null);
      const chatMessage = get(ev, ['object', 'message', 'message'], null);
      const messageRaw =
        typeof tipMessage === 'string' && tipMessage.trim() !== ''
          ? tipMessage.trim()
          : typeof chatMessage === 'string'
            ? chatMessage.trim()
            : '';
      const message = messageRaw || 'No message provided.';

      const id = (toastIdRef.current += 1);
      const toast: TipToast = { id, username, message, tokens: tokens ?? 0 };

      setTipToasts((prev) => {
        const next = [...prev, toast];
        const overflow = Math.max(0, next.length - 3);
        if (overflow > 0) {
          const removed = next.slice(0, overflow);
          for (const item of removed) {
            const timer = toastTimersRef.current.get(item.id);
            if (timer != null) {
              window.clearTimeout(timer);
              toastTimersRef.current.delete(item.id);
            }
          }
        }
        return next.slice(overflow);
      });

      const timer = window.setTimeout(() => {
        setTipToasts((prev) => prev.filter((t) => t.id !== id));
        toastTimersRef.current.delete(id);
      }, 5000);
      toastTimersRef.current.set(id, timer);
    };

    return () => {
      es.close();
    };
  }, [backendReady]);

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
    <PlaybackProvider>
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
      {tipToasts.length ? (
        <div className="tipToastStack" role="status" aria-live="polite">
          {tipToasts.map((toast) => (
            <div key={toast.id} className="tipToast">
              <div className="tipToastTitle">Song request from {toast.username} · {toast.tokens} tokens</div>
              <div className="tipToastMessage">{toast.message}</div>
            </div>
          ))}
        </div>
      ) : null}
    </PlaybackProvider>
  );
}
