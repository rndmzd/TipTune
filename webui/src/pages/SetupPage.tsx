import { useEffect, useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';

import { apiJson } from '../api';
import { HeaderBar } from '../components/HeaderBar';

type ConfigResp = { ok: true; config: Record<string, Record<string, string>> };
type SpotifyAuthStatusResp = {
  ok: true;
  configured: boolean;
  authorized: boolean;
  client_ready: boolean;
  redirect_url?: string;
  in_progress: boolean;
  auth_url?: string | null;
  error?: string | null;
};

type SpotifyAuthStartResp = { ok: true; auth_url: string };

function asBool(v: unknown): boolean {
  if (typeof v === 'boolean') return v;
  const s = String(v ?? '').trim().toLowerCase();
  return s === '1' || s === 'true' || s === 'yes' || s === 'on';
}

export function SetupPage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const rerun = searchParams.get('rerun') === '1' || searchParams.get('rerun') === 'true';

  const titleSuffix = useMemo(() => (rerun ? ' (rerun)' : ''), [rerun]);

  const [statusText, setStatusText] = useState<'loading' | 'complete' | 'incomplete'>('loading');
  const [statusMsg, setStatusMsg] = useState('');

  const [sp, setSp] = useState<SpotifyAuthStatusResp | null>(null);
  const [spBusy, setSpBusy] = useState(false);
  const [spMsg, setSpMsg] = useState('');

  async function refresh() {
    const data = await apiJson<ConfigResp>('/api/config');
    const setupComplete = asBool((data.config?.General || {}).setup_complete);
    setStatusText(setupComplete ? 'complete' : 'incomplete');
  }

  async function refreshSpotifyStatus() {
    try {
      const data = await apiJson<SpotifyAuthStatusResp>('/api/spotify/auth/status');
      setSp(data);
    } catch (e: any) {
      setSp(null);
      setSpMsg(`Error loading Spotify status: ${e?.message ? e.message : String(e)}`);
    }
  }

  async function openExternalUrl(url: string) {
    try {
      const mod: any = await import('@tauri-apps/plugin-shell');
      if (mod && typeof mod.open === 'function') {
        await mod.open(url);
        return;
      }
    } catch {
    }

    try {
      window.open(url, '_blank', 'noopener,noreferrer');
    } catch {
      window.location.href = url;
    }
  }

  async function startSpotifyAuth() {
    setSpBusy(true);
    setSpMsg('Starting Spotify login...');
    try {
      const data = await apiJson<SpotifyAuthStartResp>('/api/spotify/auth/start', { method: 'POST' });
      await openExternalUrl(data.auth_url);
      setSpMsg('Browser opened. Complete Spotify login, then return here.');
    } catch (e: any) {
      setSpMsg(`Error: ${e?.message ? e.message : String(e)}`);
    } finally {
      setSpBusy(false);
      await refreshSpotifyStatus().catch(() => {});
    }
  }

  useEffect(() => {
    refresh().catch(() => setStatusText('incomplete'));
    refreshSpotifyStatus().catch(() => {});
  }, []);

  useEffect(() => {
    const t = window.setInterval(() => {
      refreshSpotifyStatus().catch(() => {});
    }, 1500);
    return () => window.clearInterval(t);
  }, []);

  return (
    <>
      <HeaderBar
        title={`Setup Wizard${titleSuffix}`}
        right={
          <div className="muted">
            <a href="/?dashboard=1">Dashboard</a>
          </div>
        }
      />

      <div className="card">
        <h2>
          Setup status: <span className="pill">{statusText}</span>
        </h2>
        <div className="muted">Use the settings page to enter your settings. When you're done, mark setup as complete.</div>
        <div className="actions">
          <button type="button" onClick={() => navigate('/settings?dashboard=1')}>
            Open Settings
          </button>
          <button
            type="button"
            onClick={async () => {
              setStatusMsg('Saving...');
              try {
                await apiJson('/api/config', {
                  method: 'POST',
                  headers: { 'Content-Type': 'application/json' },
                  body: JSON.stringify({ General: { setup_complete: 'true' } }),
                });
                setStatusMsg('Setup marked complete.');
                navigate('/');
              } catch (e: any) {
                setStatusMsg(`Error: ${e?.message ? e.message : String(e)}`);
              }
            }}
          >
            Mark Setup Complete
          </button>
        </div>
        <div className="muted">{statusMsg}</div>
      </div>

      <div className="card" style={{ marginTop: 16 }}>
        <h2>
          Spotify: <span className="pill">{sp ? (sp.authorized ? 'authorized' : sp.configured ? 'not authorized' : 'not configured') : 'loading'}</span>
        </h2>
        <div className="muted">
          Redirect URL: <code>{sp?.redirect_url || '(not set)'}</code>
        </div>
        <div className="actions">
          <button type="button" onClick={() => refreshSpotifyStatus().catch(() => {})} disabled={spBusy}>
            Refresh
          </button>
          <button
            type="button"
            onClick={() => startSpotifyAuth().catch(() => {})}
            disabled={spBusy || !sp?.configured || !!sp?.in_progress}
          >
            Connect Spotify
          </button>
        </div>
        {sp?.error ? <div className="muted">Error: {String(sp.error)}</div> : null}
        {spMsg ? <div className="muted">{spMsg}</div> : null}
      </div>
    </>
  );
}
