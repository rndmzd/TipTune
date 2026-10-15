import { useEffect, useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';

import { apiJson } from '../api';
import { HeaderBar } from '../components/HeaderBar';
import { OverlaySettings } from '../components/OverlaySettings';
import { ObsSetup } from '../components/ObsSetup';
import { ObsAudioSettings } from '../components/ObsAudioSettings';
import type { ObsStatus as ObsStatusResp } from '../types';

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

type ObsScenesResp = {
  ok: boolean;
  scenes?: string[];
  error?: string;
};

type SetupStatusResp = {
  ok: true;
  setup_complete: boolean;
  events_configured: boolean;
  openai_configured: boolean;
  google_configured: boolean;
  obs_configured: boolean;
};

function norm(s: unknown): string {
  return String(s ?? '').trim();
}

function asBool(v: unknown): boolean {
  if (typeof v === 'boolean') return v;
  const s = String(v ?? '').trim().toLowerCase();
  return s === '1' || s === 'true' || s === 'yes' || s === 'on';
}

function humanizeKey(raw: string) {
  const cleaned = (raw || '')
    .replace(/[_\-]+/g, ' ')
    .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
    .trim();

  const words = cleaned.split(/\s+/g).filter(Boolean);
  const upper = new Set(['url', 'api', 'id', 'obs', 'cx', 'ai']);

  return words
    .map((w) => {
      const lw = w.toLowerCase();
      if (upper.has(lw)) return lw.toUpperCase();
      return lw.charAt(0).toUpperCase() + lw.slice(1);
    })
    .join(' ');
}

type WizardStepKey = 'spotify' | 'events' | 'openai' | 'google' | 'obs' | 'obs_sources' | 'general';
const BASE_STEPS: { key: WizardStepKey; title: string }[] = [
  { key: 'spotify', title: 'Spotify' },
  { key: 'events', title: 'Events API' },
  { key: 'openai', title: 'OpenAI API' },
  { key: 'google', title: 'Google' },
  { key: 'obs', title: 'OBS' },
  { key: 'general', title: 'General Settings' },
];

const DEFAULT_CFG: Record<string, Record<string, string>> = {
  Spotify: {
    redirect_url: 'http://127.0.0.1:8888/callback',
  },
  Music: {
    source: 'spotify',
  },
  'Events API': {
    max_requests_per_minute: '1000',
  },
  OpenAI: {
    model: 'gpt-6-luna',
  },
  OBS: {
    enabled: 'false',
    host: '127.0.0.1',
    port: '4455',
  },
  General: {
    request_overlay_duration: '10',
    allow_source_override_in_request_message: 'true',
  },
};

function withDefaults(inputCfg: Record<string, Record<string, string>>): Record<string, Record<string, string>> {
  const cfg = inputCfg || {};

  const spotify = cfg.Spotify || {};
  const music = cfg.Music || {};
  const events = cfg['Events API'] || {};
  const openai = cfg.OpenAI || {};
  const obs = cfg.OBS || {};
  const general = cfg.General || {};

  return {
    ...cfg,
    Spotify: {
      ...spotify,
      redirect_url: norm(spotify.redirect_url) || DEFAULT_CFG.Spotify.redirect_url,
    },
    Music: {
      ...music,
      source: norm(music.source) || DEFAULT_CFG.Music.source,
    },
    'Events API': {
      ...events,
      max_requests_per_minute: norm(events.max_requests_per_minute) || DEFAULT_CFG['Events API'].max_requests_per_minute,
    },
    OpenAI: {
      ...openai,
      model: norm(openai.model) || DEFAULT_CFG.OpenAI.model,
    },
    OBS: {
      ...obs,
      enabled: norm(obs.enabled) || DEFAULT_CFG.OBS.enabled,
      host: norm(obs.host) || DEFAULT_CFG.OBS.host,
      port: norm(obs.port) || DEFAULT_CFG.OBS.port,
    },
    General: {
      ...general,
      request_overlay_duration: norm(general.request_overlay_duration) || DEFAULT_CFG.General.request_overlay_duration,
      allow_source_override_in_request_message: norm(general.allow_source_override_in_request_message) || DEFAULT_CFG.General.allow_source_override_in_request_message,
    },
  };
}

function obsSignature(cfg: Record<string, Record<string, string>>) {
  return JSON.stringify([cfg.OBS?.scene_name || '', asBool(cfg.OBS?.use_virtual_cable), cfg.Overlay || {}, cfg.General?.request_overlay_duration || '10']);
}

export function SetupPage({ audioOutputError = '', audioOutputActive }: { audioOutputError?: string; audioOutputActive?: boolean | null }) {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const rerun = searchParams.get('rerun') === '1' || searchParams.get('rerun') === 'true';
  const titleSuffix = useMemo(() => (rerun ? ' (rerun)' : ''), [rerun]);

  const [stepIdx, setStepIdx] = useState(0);

  const [cfg, setCfg] = useState<Record<string, Record<string, string>>>(DEFAULT_CFG);
  const [secrets, setSecrets] = useState({
    eventsUrl: '',
    openaiKey: '',
    googleKey: '',
    obsPassword: '',
  });
  const [status, setStatus] = useState('');

  const [setupStatus, setSetupStatus] = useState<SetupStatusResp | null>(null);

  const [sp, setSp] = useState<SpotifyAuthStatusResp | null>(null);
  const [spBusy, setSpBusy] = useState(false);
  const [spMsg, setSpMsg] = useState('');

  const [obsStatus, setObsStatus] = useState<ObsStatusResp | null>(null);
  const [obsMsg, setObsMsg] = useState('');
  const [obsSaveBusy, setObsSaveBusy] = useState(false);
  const [obsBaseline, setObsBaseline] = useState('');

  const [obsScenes, setObsScenes] = useState<string[]>([]);
  const [obsScenesMsg, setObsScenesMsg] = useState('');

  const v = (section: string, key: string) => norm((cfg[section] || {})[key]);

  const obsEnabled = asBool(v('OBS', 'enabled') || DEFAULT_CFG.OBS.enabled);

  const steps = useMemo(() => {
    const out: { key: WizardStepKey; title: string }[] = [];
    for (const s of BASE_STEPS) {
      out.push(s);
      if (s.key === 'obs') out.push({ key: 'obs_sources', title: 'OBS Sources' });
    }
    return out;
  }, []);

  useEffect(() => {
    if (stepIdx >= steps.length) setStepIdx(Math.max(0, steps.length - 1));
  }, [stepIdx, steps.length]);

  async function loadConfig() {
    const data = await apiJson<ConfigResp>('/api/config');
    const loaded = withDefaults(data.config || {});
    setCfg(loaded);
    setObsBaseline(obsSignature(loaded));
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

  async function loadObsStatus() {
    try {
      const data = await apiJson<ObsStatusResp>('/api/obs/status');
      setObsStatus(data);
      setObsMsg('');
    } catch (e: any) {
      setObsStatus(null);
      setObsMsg(`Error loading OBS status: ${e?.message ? e.message : String(e)}`);
    }
  }

  async function loadObsScenes() {
    setObsScenesMsg('Loading scenes...');
    try {
      const host = norm(v('OBS', 'host'));
      const port = norm(v('OBS', 'port'));
      const qs = `?host=${encodeURIComponent(host)}&port=${encodeURIComponent(port)}`;
      const resp = await apiJson<ObsScenesResp>(`/api/obs/scenes${qs}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password: secrets.obsPassword }),
      });

      const scenes = Array.isArray(resp.scenes) ? resp.scenes.filter((s) => typeof s === 'string' && norm(s) !== '') : [];
      scenes.sort((a, b) => a.localeCompare(b));
      setObsScenes(scenes);
      setObsScenesMsg(scenes.length ? '' : 'No scenes returned from OBS.');
    } catch (e: any) {
      setObsScenes([]);
      setObsScenesMsg(`Error loading scenes: ${e?.message ? e.message : String(e)}`);
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
      await savePartial({
        Spotify: {
          client_id: v('Spotify', 'client_id'),
          redirect_url: v('Spotify', 'redirect_url'),
        },
      });
      await loadConfig();
      await refreshSpotifyStatus().catch(() => {});

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

  async function saveObsSetup() {
    setObsSaveBusy(true); setStatus('Saving…');
    try {
      await savePartial({ Overlay: cfg.Overlay || {}, General: { request_overlay_duration: v('General', 'request_overlay_duration') }, OBS: {
        scene_name: v('OBS', 'scene_name'), use_virtual_cable: asBool(v('OBS', 'use_virtual_cable')) ? 'true' : 'false',
      } });
      await loadConfig();
      await loadObsStatus();
      setStatus('Saved.');
    } finally { setObsSaveBusy(false); }
  }

  async function onNext() {
    setStatus('Saving...');
    setSpMsg('');

    const step = steps[stepIdx]?.key;
    try {
      if (step === 'spotify') {
        await savePartial({
          Spotify: {
            client_id: v('Spotify', 'client_id'),
            redirect_url: v('Spotify', 'redirect_url'),
          },
        });
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
      } else if (step === 'obs') {
        await savePartial({
          OBS: {
            enabled: v('OBS', 'enabled'),
            host: v('OBS', 'host'),
            port: v('OBS', 'port'),
            password: secrets.obsPassword,
          },
        });
        setSecrets((s) => ({ ...s, obsPassword: '' }));
        await loadConfig();
        await loadSetupStatus().catch(() => {});

        // After saving connection info, try to load scene list for the dropdown.
        if (obsEnabled) {
          await loadObsScenes().catch(() => {});
        }
      } else if (step === 'obs_sources') {
        await saveObsSetup();
      } else if (step === 'general') {
        await savePartial({
          Music: {
            source: v('Music', 'source') || DEFAULT_CFG.Music.source,
          },
          General: {
            song_cost: v('General', 'song_cost'),
            allow_source_override_in_request_message:
              v('General', 'allow_source_override_in_request_message') || DEFAULT_CFG.General.allow_source_override_in_request_message,
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
      setStepIdx((i) => Math.min(i + 1, steps.length - 1));
    } catch (e: any) {
      setStatus(`Error: ${e?.message ? e.message : String(e)}`);
    }
  }

  function onBack() {
    setStatus('');
    setStepIdx((i) => Math.max(i - 1, 0));
  }

  function onSkipOpenAI() {
    if (currentStep !== 'openai') return;

    if (!openaiOk) {
      const ok = window.confirm(
        'OpenAI settings are required for TipTune to function correctly. OpenAI is used to extract song information from request messages.\n\nIf you continue without configuring OpenAI, song request parsing will not work.\n\nDo you want to continue anyway?'
      );
      if (!ok) return;
    }

    setStatus('');
    setStepIdx((i) => Math.min(i + 1, steps.length - 1));
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

  const currentStep = steps[stepIdx]?.key;

  useEffect(() => {
    if (currentStep !== 'obs_sources') return;
    if (!obsEnabled) return;
    let stopped = false;
    let timer: number | undefined;
    async function poll() {
      await loadObsStatus();
      if (!stopped) timer = window.setTimeout(poll, 5000);
    }
    poll();
    return () => { stopped = true; if (timer) window.clearTimeout(timer); };
  }, [currentStep, obsEnabled]);

  useEffect(() => {
    if (currentStep !== 'obs_sources') {
      setObsScenesMsg('');
      setObsScenes([]);
      return;
    }
    if (!obsEnabled) return;
    // If a scene name is already set, and we have host/port, try to preload dropdown.
    const host = norm(v('OBS', 'host'));
    const port = norm(v('OBS', 'port'));
    if (host && port) {
      loadObsScenes().catch(() => {});
    }
  }, [currentStep, obsEnabled]);

  const setupComplete = setupStatus ? !!setupStatus.setup_complete : asBool(v('General', 'setup_complete'));

  const spotifyOk =
    norm(v('Spotify', 'client_id')) !== '' &&
    norm(v('Spotify', 'redirect_url')) !== '';
  const eventsOk = norm(secrets.eventsUrl) !== '' || !!setupStatus?.events_configured;
  const openaiOk = norm(secrets.openaiKey) !== '' || !!setupStatus?.openai_configured;

  const googleCx = norm(v('Search', 'google_cx'));
  const googleKey = norm(secrets.googleKey);
  const googleOk =
    !!setupStatus?.google_configured ||
    (googleCx === '' && googleKey === '') ||
    (googleCx !== '' && googleKey !== '');

  const obsHost = norm(v('OBS', 'host'));
  const obsPort = norm(v('OBS', 'port'));
  const obsSceneName = norm(v('OBS', 'scene_name'));
  const obsOk = !obsEnabled || (obsHost !== '' && obsPort !== '');
  const obsSourcesOk = !obsEnabled || obsSceneName !== '';

  const obsSetupDirty = !!obsBaseline && obsSignature(cfg) !== obsBaseline;

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
            : currentStep === 'obs'
              ? obsOk
              : currentStep === 'obs_sources'
                ? obsSourcesOk
                : currentStep === 'general'
                  ? generalOk
                  : false;

  const nextLabel = currentStep === 'general' ? 'Finish' : 'Next';

  return (
    <>
      <HeaderBar
        title={`Setup Wizard${titleSuffix}`}
      />

      <div className="card">
        <h2>
          Setup status:{' '}
          <span className={setupComplete ? 'pill pillSuccess' : 'pill pillWarn'}>{setupComplete ? 'complete' : 'incomplete'}</span>
        </h2>
        <div className="muted">
          Step {stepIdx + 1} of {steps.length}: <span className="pill pillInfo">{steps[stepIdx]?.title}</span>
        </div>
        <div className="actions">
          <button type="button" onClick={onBack} disabled={stepIdx === 0}>
            Back
          </button>
          <button type="button" onClick={() => onNext().catch(() => {})} disabled={!canNext || obsSaveBusy}>
            {nextLabel}
          </button>
          {currentStep === 'openai' && !openaiOk ? (
            <button type="button" onClick={onSkipOpenAI}>
              Skip
            </button>
          ) : null}
          {(currentStep === 'spotify' && !spotifyOk || currentStep === 'events' && !eventsOk) ? (
            <button type="button" onClick={async () => {
              try {
                if (currentStep === 'spotify') {
                  await savePartial({ Music: { source: 'youtube' } });
                  await loadConfig();
                }
                setStatus('You can connect this later in Settings.');
                setStepIdx((i) => i + 1);
              } catch (error: any) { setStatus(`Error: ${error?.message || error}`); }
            }}>Set up later</button>
          ) : null}
          <button type="button" onClick={() => navigate('/settings?dashboard=1')}>
            Open Full Settings
          </button>
          <span className="muted">{status}</span>
        </div>
      </div>

      {currentStep === 'spotify' ? (
        <div className="card" style={{ marginTop: 16 }}>
          <h2>
            Spotify:{' '}
            <span
              className={
                sp
                  ? sp.authorized
                    ? 'pill pillSuccess'
                    : sp.configured
                      ? 'pill pillWarn'
                      : 'pill pillError'
                  : 'pill pillNeutral'
              }
            >
              {sp ? (sp.authorized ? 'authorized' : sp.configured ? 'not authorized' : 'not configured') : 'loading'}
            </span>
          </h2>

          <label>{humanizeKey('client_id')}</label>
          <input
            type="text"
            value={v('Spotify', 'client_id')}
            onChange={(e) => setCfg((c) => ({ ...c, Spotify: { ...(c.Spotify || {}), client_id: e.target.value } }))}
          />
          <div className="muted">Get this from your Spotify Developer Dashboard: create an app and copy the Client ID.</div>

          <label>{humanizeKey('redirect_url')}</label>
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
            <button type="button" onClick={() => startSpotifyAuth().catch(() => {})} disabled={spBusy || !spotifyOk || !!sp?.in_progress}>
              Connect Spotify
            </button>
          </div>
          {sp?.error ? <div className="muted">Error: <span className="pill pillError">{String(sp.error)}</span></div> : null}
          {spMsg ? <div className="muted">{spMsg}</div> : null}
          <div className="muted" style={{ marginTop: 8 }}>
            You can proceed after entering Client ID + redirect URL. Connecting Spotify is recommended but not required to click Next.
          </div>
          <p className="muted">After connecting the API, open the Spotify tab and sign in to its embedded player with the same account. You can set up Spotify later and start with YouTube.</p>
        </div>
      ) : null}

      {currentStep === 'obs' ? (
        <div className="card" style={{ marginTop: 16 }}>
          <h2>OBS</h2>

          <label>{humanizeKey('enabled')}</label>
          <select
            value={(v('OBS', 'enabled') || DEFAULT_CFG.OBS.enabled).toLowerCase()}
            onChange={(e) => setCfg((c) => ({ ...c, OBS: { ...(c.OBS || {}), enabled: e.target.value } }))}
          >
            <option value="true">true</option>
            <option value="false">false</option>
          </select>

          <label>{humanizeKey('host')}</label>
          <input
            type="text"
            value={v('OBS', 'host')}
            onChange={(e) => setCfg((c) => ({ ...c, OBS: { ...(c.OBS || {}), host: e.target.value } }))}
          />

          <label>{humanizeKey('port')}</label>
          <input
            type="text"
            value={v('OBS', 'port')}
            onChange={(e) => setCfg((c) => ({ ...c, OBS: { ...(c.OBS || {}), port: e.target.value } }))}
          />

          <label>{humanizeKey('password')} (secret)</label>
          <input
            type="password"
            placeholder={setupStatus?.obs_configured ? '(leave blank to keep existing)' : ''}
            value={secrets.obsPassword}
            onChange={(e) => setSecrets((s) => ({ ...s, obsPassword: e.target.value }))}
          />
          <div className="muted">This is the password configured in OBS → Tools → WebSocket Server Settings.</div>
        </div>
      ) : null}

      {currentStep === 'obs_sources' ? (
        <div className="wizard-obs-setup">
          <div className="actions">
            <button type="button" disabled={obsSaveBusy || !obsSetupDirty} onClick={() => saveObsSetup().catch((error) => setStatus(`Error: ${error?.message || error}`))}>
              {obsSaveBusy ? 'Saving…' : 'Save OBS setup'}
            </button>
            {obsSetupDirty ? <span className="muted">Save before adding sources to OBS.</span> : null}
          </div>
          <ObsSetup cfg={cfg} status={obsStatus} statusError={obsMsg} dirty={obsSetupDirty || obsSaveBusy} pending={obsSetupDirty}
            onRefreshStatus={loadObsStatus}
            onChange={(section, key, value) => setCfg((c) => ({ ...c, [section]: { ...(c[section] || {}), [key]: value } }))}
            sceneControls={obsEnabled ? <>
              {obsStatus?.connected ? <dl className="overlay-scene-status">
                <dt>Current scene</dt><dd>{obsStatus.status?.current_scene || '(unknown)'}</dd>
                <dt>Main scene</dt><dd>{obsStatus.status?.main_scene || '(unknown)'}</dd>
              </dl> : null}
              <label htmlFor="wizard-overlay-scene">Overlay scene</label>
              <select id="wizard-overlay-scene" value={obsSceneName} disabled={!obsStatus?.connected}
                onChange={(e) => setCfg((c) => ({ ...c, OBS: { ...(c.OBS || {}), scene_name: e.target.value } }))}>
                <option value="">(select a scene)</option>
                {Array.from(new Set([obsSceneName, ...obsScenes].filter(Boolean))).map((scene) => <option key={scene} value={scene}>{scene}</option>)}
              </select>
              <div className="actions"><button type="button" disabled={!obsStatus?.connected} onClick={() => loadObsScenes()}>Refresh scenes</button></div>
              {obsStatus?.connected && obsScenesMsg ? <p className="muted" role="status">{obsScenesMsg}</p> : null}
            </> : null} />
          {obsEnabled ? <ObsAudioSettings status={obsStatus} error={obsMsg} audioOutputError={audioOutputError} outputActive={audioOutputActive}
            enabled={asBool(v('OBS', 'use_virtual_cable'))} pending={obsSetupDirty} onboarding
            dirty={obsSetupDirty || obsSaveBusy} onSave={saveObsSetup} onRefresh={loadObsStatus}
            onEnabledChange={(enabled) => setCfg((c) => ({ ...c, OBS: { ...(c.OBS || {}), use_virtual_cable: enabled ? 'true' : 'false' } }))} /> : null}
          <div className="card">
            <OverlaySettings cfg={cfg} status={obsStatus} dirty={obsSetupDirty}
              onChange={(section, key, value) => setCfg((c) => ({ ...c, [section]: { ...(c[section] || {}), [key]: value } }))} />
          </div>
        </div>
      ) : null}

      {currentStep === 'events' ? (
        <div className="card" style={{ marginTop: 16 }}>
          <h2>Events API</h2>

          <label>URL (secret)</label>
          <input
            type="password"
            placeholder={setupStatus?.events_configured ? '(leave blank to keep existing)' : ''}
            value={secrets.eventsUrl}
            onChange={(e) => setSecrets((s) => ({ ...s, eventsUrl: e.target.value }))}
          />
          <div className="muted">
            Get this from your Chaturbate Events API page. It looks like:
            <code style={{ display: 'block', marginTop: 6 }}>
              https://eventsapi.chaturbate.com/events/&lt;yourusername&gt;/&lt;your-token&gt;/
            </code>
          </div>

          <label>{humanizeKey('max_requests_per_minute')}</label>
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

          <label>API key (secret)</label>
          <input
            type="password"
            placeholder={setupStatus?.openai_configured ? '(leave blank to keep existing)' : ''}
            value={secrets.openaiKey}
            onChange={(e) => setSecrets((s) => ({ ...s, openaiKey: e.target.value }))}
          />
          <div className="muted">Create an API key in your OpenAI dashboard and paste it here.</div>

          <label>Model</label>
          <input
            type="text"
            value={v('OpenAI', 'model') || 'gpt-6-luna'}
            onChange={(e) => setCfg((c) => ({ ...c, OpenAI: { ...(c.OpenAI || {}), model: e.target.value } }))}
          />
          <div className="muted">Default: gpt-6-luna. You can also use gpt-6-sol or gpt-6-astra if available to your account. Requires Responses Structured Outputs.</div>
        </div>
      ) : null}

      {currentStep === 'google' ? (
        <div className="card" style={{ marginTop: 16 }}>
          <h2>Google</h2>

          <label>{humanizeKey('google_api_key')} (secret)</label>
          <input
            type="password"
            placeholder={setupStatus?.google_configured ? '(leave blank to keep existing)' : ''}
            value={secrets.googleKey}
            onChange={(e) => setSecrets((s) => ({ ...s, googleKey: e.target.value }))}
          />
          <div className="muted">
            In Google Cloud Console, create/choose a project → APIs & Services → Credentials → Create credentials → API key.
          </div>

          <label>{humanizeKey('google_cx')}</label>
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

          <label>Music source</label>
          <select value={v('Music', 'source') || DEFAULT_CFG.Music.source} onChange={(e) => setCfg((c) => ({ ...c, Music: { ...(c.Music || {}), source: e.target.value } }))}>
            <option value="spotify">Spotify</option>
            <option value="youtube">YouTube</option>
          </select>

          <label>{humanizeKey('song_cost')}</label>
          <input
            type="text"
            value={v('General', 'song_cost')}
            onChange={(e) => setCfg((c) => ({ ...c, General: { ...(c.General || {}), song_cost: e.target.value } }))}
          />
          <div className="muted">Exact tip amount (in tokens) required to request one song.</div>

          <label>{humanizeKey('skip_song_cost')}</label>
          <input
            type="text"
            value={v('General', 'skip_song_cost')}
            onChange={(e) => setCfg((c) => ({ ...c, General: { ...(c.General || {}), skip_song_cost: e.target.value } }))}
          />
          <div className="muted">Tip amount (in tokens) that triggers a “skip current song” action.</div>

          <label>{humanizeKey('allow_source_override_in_request_message')}</label>
          <select
            value={(v('General', 'allow_source_override_in_request_message') || DEFAULT_CFG.General.allow_source_override_in_request_message).toLowerCase()}
            onChange={(e) =>
              setCfg((c) => ({
                ...c,
                General: { ...(c.General || {}), allow_source_override_in_request_message: e.target.value },
              }))
            }
          >
            <option value="true">true</option>
            <option value="false">false</option>
          </select>
          <div className="muted">If true, users can include “spotify” or “youtube” in their request message to override the music source for that request.</div>

          <label>OBS overlay duration</label>
          <input
            type="text"
            value={v('General', 'request_overlay_duration')}
            onChange={(e) => setCfg((c) => ({ ...c, General: { ...(c.General || {}), request_overlay_duration: e.target.value } }))}
          />
          <div className="muted">How long (seconds) OBS overlays stay visible.</div>
        </div>
      ) : null}
    </>
  );
}
