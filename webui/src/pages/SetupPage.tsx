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

type SetupStatusResp = {
  ok: true;
  setup_complete: boolean;
  events_configured: boolean;
  openai_configured: boolean;
  google_configured: boolean;
};

function norm(s: unknown): string {
  return String(s ?? '').trim();
}

function asBool(v: unknown): boolean {
  if (typeof v === 'boolean') return v;
  const s = String(v ?? '').trim().toLowerCase();
  return s === '1' || s === 'true' || s === 'yes' || s === 'on';
}

type WizardStepKey = 'spotify' | 'events' | 'openai' | 'google' | 'general';
const STEPS: { key: WizardStepKey; title: string }[] = [
  { key: 'spotify', title: 'Spotify' },
  { key: 'events', title: 'Events API' },
  { key: 'openai', title: 'OpenAI API' },
  { key: 'google', title: 'Google' },
  { key: 'general', title: 'General Settings' },
];

export function SetupPage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const rerun = searchParams.get('rerun') === '1' || searchParams.get('rerun') === 'true';
  const titleSuffix = useMemo(() => (rerun ? ' (rerun)' : ''), [rerun]);

  const [stepIdx, setStepIdx] = useState(0);

  const [cfg, setCfg] = useState<Record<string, Record<string, string>>>({});
  const [secrets, setSecrets] = useState({
    eventsUrl: '',
    openaiKey: '',
    spotifySecret: '',
    googleKey: '',
  });
  const [status, setStatus] = useState('');

  const [setupStatus, setSetupStatus] = useState<SetupStatusResp | null>(null);

  const [sp, setSp] = useState<SpotifyAuthStatusResp | null>(null);
  const [spBusy, setSpBusy] = useState(false);
  const [spMsg, setSpMsg] = useState('');

  const v = (section: string, key: string) => norm((cfg[section] || {})[key]);

  async function loadConfig() {
    const data = await apiJson<ConfigResp>('/api/config');
    setCfg(data.config || {});
  }

  async function loadSetupStatus() {
    const data = await apiJson<SetupStatusResp>('/api/setup/status');
    setSetupStatus(data);
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

  async function savePartial(updates: Record<string, Record<string, string>>) {
    await apiJson('/api/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(updates),
    });
  }

  async function onNext() {
    setStatus('Saving...');
    setSpMsg('');

    const step = STEPS[stepIdx]?.key;
    try {
      if (step === 'spotify') {
        await savePartial({
          Spotify: {
            client_id: v('Spotify', 'client_id'),
            client_secret: secrets.spotifySecret,
            redirect_url: v('Spotify', 'redirect_url'),
          },
        });
        setSecrets((s) => ({ ...s, spotifySecret: '' }));
        await loadConfig();
        await loadSetupStatus().catch(() => {});
        await refreshSpotifyStatus();
      } else if (step === 'events') {
        await savePartial({
          'Events API': {
            url: secrets.eventsUrl,
            max_requests_per_minute: v('Events API', 'max_requests_per_minute'),
          },
        });
        setSecrets((s) => ({ ...s, eventsUrl: '' }));
        await loadConfig();
        await loadSetupStatus().catch(() => {});
      } else if (step === 'openai') {
        await savePartial({
          OpenAI: {
            api_key: secrets.openaiKey,
            model: v('OpenAI', 'model'),
          },
        });
        setSecrets((s) => ({ ...s, openaiKey: '' }));
        await loadConfig();
        await loadSetupStatus().catch(() => {});
      } else if (step === 'google') {
        await savePartial({
          Search: {
            google_api_key: secrets.googleKey,
            google_cx: v('Search', 'google_cx'),
          },
        });
        setSecrets((s) => ({ ...s, googleKey: '' }));
        await loadConfig();
        await loadSetupStatus().catch(() => {});
      } else if (step === 'general') {
        await savePartial({
          General: {
            song_cost: v('General', 'song_cost'),
            skip_song_cost: v('General', 'skip_song_cost'),
            request_overlay_duration: v('General', 'request_overlay_duration'),
            setup_complete: 'true',
          },
        });
        await loadConfig();
        await loadSetupStatus().catch(() => {});
        navigate('/');
        return;
      }

      setStatus('Saved.');
      setStepIdx((i) => Math.min(i + 1, STEPS.length - 1));
    } catch (e: any) {
      setStatus(`Error: ${e?.message ? e.message : String(e)}`);
    }
  }

  function onBack() {
    setStatus('');
    setStepIdx((i) => Math.max(i - 1, 0));
  }

  useEffect(() => {
    loadConfig().catch((e) => setStatus(`Error loading config: ${e?.message ? e.message : String(e)}`));
    loadSetupStatus().catch(() => {});
    refreshSpotifyStatus().catch(() => {});
  }, []);

  useEffect(() => {
    const t = window.setInterval(() => {
      refreshSpotifyStatus().catch(() => {});
    }, 1500);
    return () => window.clearInterval(t);
  }, []);

  const currentStep = STEPS[stepIdx]?.key;

  const setupComplete = setupStatus ? !!setupStatus.setup_complete : asBool(v('General', 'setup_complete'));

  const spotifyConfiguredFromStatus = !!sp?.configured;
  const spotifyOk =
    norm(v('Spotify', 'client_id')) !== '' &&
    (norm(secrets.spotifySecret) !== '' || spotifyConfiguredFromStatus) &&
    norm(v('Spotify', 'redirect_url')) !== '';
  const eventsOk = norm(secrets.eventsUrl) !== '' || !!setupStatus?.events_configured;
  const openaiOk = norm(secrets.openaiKey) !== '' || !!setupStatus?.openai_configured;

  const googleCx = norm(v('Search', 'google_cx'));
  const googleKey = norm(secrets.googleKey);
  const googleOk =
    !!setupStatus?.google_configured ||
    (googleCx === '' && googleKey === '') ||
    (googleCx !== '' && googleKey !== '');

  const generalOk =
    norm(v('General', 'song_cost')) !== '' && norm(v('General', 'skip_song_cost')) !== '' && norm(v('General', 'request_overlay_duration')) !== '';

  const canNext =
    currentStep === 'spotify'
      ? spotifyOk
      : currentStep === 'events'
        ? eventsOk
        : currentStep === 'openai'
          ? openaiOk
          : currentStep === 'google'
            ? googleOk
            : currentStep === 'general'
              ? generalOk
              : false;

  const nextLabel = currentStep === 'general' ? 'Finish' : 'Next';

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
          Setup status: <span className="pill">{setupComplete ? 'complete' : 'incomplete'}</span>
        </h2>
        <div className="muted">
          Step {stepIdx + 1} of {STEPS.length}: <span className="pill">{STEPS[stepIdx]?.title}</span>
        </div>
        <div className="actions">
          <button type="button" onClick={onBack} disabled={stepIdx === 0}>
            Back
          </button>
          <button type="button" onClick={() => onNext().catch(() => {})} disabled={!canNext}>
            {nextLabel}
          </button>
          <button type="button" onClick={() => navigate('/settings?dashboard=1')}>
            Open Full Settings
          </button>
          <span className="muted">{status}</span>
        </div>
      </div>

      {currentStep === 'spotify' ? (
        <div className="card" style={{ marginTop: 16 }}>
          <h2>
            Spotify: <span className="pill">{sp ? (sp.authorized ? 'authorized' : sp.configured ? 'not authorized' : 'not configured') : 'loading'}</span>
          </h2>

          <label>Spotify client_id</label>
          <input
            type="text"
            value={v('Spotify', 'client_id')}
            onChange={(e) => setCfg((c) => ({ ...c, Spotify: { ...(c.Spotify || {}), client_id: e.target.value } }))}
          />
          <div className="muted">Get this from your Spotify Developer Dashboard: create an app and copy the Client ID.</div>

          <label>Spotify client_secret (secret)</label>
          <input
            type="password"
            placeholder="(leave blank to keep existing)"
            value={secrets.spotifySecret}
            onChange={(e) => setSecrets((s) => ({ ...s, spotifySecret: e.target.value }))}
          />
          <div className="muted">In the same Spotify app page, click “Show Client Secret” and copy it.</div>

          <label>Spotify redirect_url</label>
          <input
            type="text"
            value={v('Spotify', 'redirect_url')}
            onChange={(e) => setCfg((c) => ({ ...c, Spotify: { ...(c.Spotify || {}), redirect_url: e.target.value } }))}
          />
          <div className="muted">
            Add this exact Redirect URI in Spotify Developer Dashboard → your app → Edit Settings → Redirect URIs.
          </div>

          <div className="muted" style={{ marginTop: 10 }}>
            Current redirect URL detected: <code>{sp?.redirect_url || '(not set)'}</code>
          </div>

          <div className="actions">
            <button type="button" onClick={() => refreshSpotifyStatus().catch(() => {})} disabled={spBusy}>
              Refresh
            </button>
            <button type="button" onClick={() => startSpotifyAuth().catch(() => {})} disabled={spBusy || !sp?.configured || !!sp?.in_progress}>
              Connect Spotify
            </button>
          </div>
          {sp?.error ? <div className="muted">Error: {String(sp.error)}</div> : null}
          {spMsg ? <div className="muted">{spMsg}</div> : null}
          <div className="muted" style={{ marginTop: 8 }}>
            You can proceed after entering credentials + redirect URL. Connecting Spotify is recommended but not required to click Next.
          </div>
        </div>
      ) : null}

      {currentStep === 'events' ? (
        <div className="card" style={{ marginTop: 16 }}>
          <h2>Events API</h2>

          <label>Events API URL (secret)</label>
          <input
            type="password"
            placeholder="(leave blank to keep existing)"
            value={secrets.eventsUrl}
            onChange={(e) => setSecrets((s) => ({ ...s, eventsUrl: e.target.value }))}
          />
          <div className="muted">
            Get this from your Chaturbate Events API page. It looks like:
            <code style={{ display: 'block', marginTop: 6 }}>
              https://eventsapi.chaturbate.com/events/&lt;yourusername&gt;/&lt;your-token&gt;/
            </code>
          </div>

          <label>max_requests_per_minute</label>
          <input
            type="text"
            value={v('Events API', 'max_requests_per_minute')}
            onChange={(e) =>
              setCfg((c) => ({ ...c, 'Events API': { ...(c['Events API'] || {}), max_requests_per_minute: e.target.value } }))
            }
          />
          <div className="muted">
            TipTune uses this to pace polling. If you’re not sure, leave the default (e.g. 1000).
          </div>
        </div>
      ) : null}

      {currentStep === 'openai' ? (
        <div className="card" style={{ marginTop: 16 }}>
          <h2>OpenAI API</h2>

          <label>OpenAI API key (secret)</label>
          <input
            type="password"
            placeholder="(leave blank to keep existing)"
            value={secrets.openaiKey}
            onChange={(e) => setSecrets((s) => ({ ...s, openaiKey: e.target.value }))}
          />
          <div className="muted">Create an API key in your OpenAI dashboard and paste it here.</div>

          <label>Model</label>
          <input
            type="text"
            value={v('OpenAI', 'model') || 'gpt-5'}
            onChange={(e) => setCfg((c) => ({ ...c, OpenAI: { ...(c.OpenAI || {}), model: e.target.value } }))}
          />
          <div className="muted">Use a supported model name for your account. If you’re unsure, keep the default.</div>
        </div>
      ) : null}

      {currentStep === 'google' ? (
        <div className="card" style={{ marginTop: 16 }}>
          <h2>Google</h2>

          <label>Google API key (secret)</label>
          <input
            type="password"
            placeholder="(leave blank to keep existing)"
            value={secrets.googleKey}
            onChange={(e) => setSecrets((s) => ({ ...s, googleKey: e.target.value }))}
          />
          <div className="muted">
            In Google Cloud Console, create/choose a project → APIs & Services → Credentials → Create credentials → API key.
          </div>

          <label>Google Custom Search Engine ID (cx)</label>
          <input
            type="text"
            value={v('Search', 'google_cx')}
            onChange={(e) => setCfg((c) => ({ ...c, Search: { ...(c.Search || {}), google_cx: e.target.value } }))}
          />
          <div className="muted">
            Create a Programmable Search Engine, then copy the “Search engine ID” (cx) into this field.
          </div>
        </div>
      ) : null}

      {currentStep === 'general' ? (
        <div className="card" style={{ marginTop: 16 }}>
          <h2>General Settings</h2>

          <label>song_cost</label>
          <input
            type="text"
            value={v('General', 'song_cost')}
            onChange={(e) => setCfg((c) => ({ ...c, General: { ...(c.General || {}), song_cost: e.target.value } }))}
          />
          <div className="muted">Tip amount (in tokens) per song request.</div>

          <label>skip_song_cost</label>
          <input
            type="text"
            value={v('General', 'skip_song_cost')}
            onChange={(e) => setCfg((c) => ({ ...c, General: { ...(c.General || {}), skip_song_cost: e.target.value } }))}
          />
          <div className="muted">Tip amount (in tokens) that triggers a “skip current song” action.</div>

          <label>request_overlay_duration</label>
          <input
            type="text"
            value={v('General', 'request_overlay_duration')}
            onChange={(e) => setCfg((c) => ({ ...c, General: { ...(c.General || {}), request_overlay_duration: e.target.value } }))}
          />
          <div className="muted">How long (seconds) the request overlay stays visible in OBS.</div>
        </div>
      ) : null}
    </>
  );
}
