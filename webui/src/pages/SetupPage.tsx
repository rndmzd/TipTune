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

type ObsSourceStatus = {
  name: string;
  input_exists: boolean;
  in_main_scene: boolean;
  present: boolean;
};

type ObsStatusResp = {
  ok: true;
  enabled: boolean;
  connected?: boolean;
  status?: {
    current_scene?: string | null;
    main_scene?: string | null;
    sources?: ObsSourceStatus[];
  };
};

type ObsEnsureResp = {
  ok: true;
  result: {
    scene?: string;
    created?: string[];
    added_to_scene?: string[];
    already_present?: string[];
    errors?: Record<string, string>;
  };
};

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

function formatObsErrors(errs: Record<string, string> | undefined): string {
  if (!errs) return '';
  const entries = Object.entries(errs);
  if (!entries.length) return '';
  return entries.map(([k, v]) => `${k}: ${v}`).join('\n');
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
    model: 'gpt-5-mini',
  },
  OBS: {
    enabled: 'false',
    host: '127.0.0.1',
    port: '4455',
  },
  General: {
    request_overlay_duration: '10',
    multi_request_tips: 'true',
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
      multi_request_tips: norm(general.multi_request_tips) || DEFAULT_CFG.General.multi_request_tips,
    },
  };
}

export function SetupPage() {
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
  const [obsEnsureMsg, setObsEnsureMsg] = useState('');
  const [obsBusy, setObsBusy] = useState(false);

  const [obsScenes, setObsScenes] = useState<string[]>([]);
  const [obsScenesMsg, setObsScenesMsg] = useState('');

  const v = (section: string, key: string) => norm((cfg[section] || {})[key]);

  const obsEnabled = asBool(v('OBS', 'enabled') || DEFAULT_CFG.OBS.enabled);

  const steps = useMemo(() => {
    if (!obsEnabled) return BASE_STEPS;
    const out: { key: WizardStepKey; title: string }[] = [];
    for (const s of BASE_STEPS) {
      out.push(s);
      if (s.key === 'obs') out.push({ key: 'obs_sources', title: 'OBS Sources' });
    }
    return out;
  }, [obsEnabled]);

  useEffect(() => {
    if (stepIdx >= steps.length) setStepIdx(Math.max(0, steps.length - 1));
  }, [stepIdx, steps.length]);

  async function loadConfig() {
    const data = await apiJson<ConfigResp>('/api/config');
    setCfg(withDefaults(data.config || {}));
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
        await savePartial({
          OBS: {
            scene_name: v('OBS', 'scene_name'),
          },
        });
        await loadConfig();
        setStatus('');
      } else if (step === 'general') {
        await savePartial({
          Music: {
            source: v('Music', 'source') || DEFAULT_CFG.Music.source,
          },
          General: {
            song_cost: v('General', 'song_cost'),
            multi_request_tips: v('General', 'multi_request_tips') || DEFAULT_CFG.General.multi_request_tips,
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
    if (currentStep !== 'obs_sources') {
      setObsEnsureMsg('');
      return;
    }
    if (!obsEnabled) return;
    loadObsStatus().catch(() => {});
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

  const requiredSources = (obsStatus?.status?.sources || []) as ObsSourceStatus[];
  const missingSources = requiredSources.filter((s) => !s.present);
  const hasMissingSources = obsEnabled && !!obsStatus?.enabled && (missingSources.length > 0);

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
        <div className="card" style={{ marginTop: 16 }}>
          <h2>
            OBS sources setup{' '}
            <span className={obsStatus?.connected ? 'pill pillSuccess' : 'pill pillError'}>
              {obsStatus?.connected ? 'connected' : 'not connected'}
            </span>
          </h2>

          <div style={{ display: 'grid', gridTemplateColumns: '140px 1fr', gap: 8, alignItems: 'baseline' }}>
            <div className="muted">Current scene</div>
            <div>
              <code>{obsStatus?.status?.current_scene || '(unknown)'}</code>
            </div>
            <div className="muted">Main scene</div>
            <div>
              <code>{obsStatus?.status?.main_scene || '(unknown)'}</code>
            </div>
          </div>

          <label style={{ marginTop: 12 }}>Overlay scene</label>
          <select
            value={obsSceneName}
            onChange={(e) => setCfg((c) => ({ ...c, OBS: { ...(c.OBS || {}), scene_name: e.target.value } }))}
            disabled={!obsEnabled}
          >
            <option value="">(select a scene)</option>
            {(obsScenes || []).map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
          <div className="muted">Pick the scene where TipTune overlays (text sources) should be created/managed.</div>

          <div className="actions" style={{ marginTop: 10 }}>
            <button type="button" onClick={() => loadObsScenes().catch(() => {})} disabled={!obsEnabled}>
              Refresh scenes
            </button>
          </div>
          {obsScenesMsg ? <div className="muted">{obsScenesMsg}</div> : null}

          <label style={{ marginTop: 12 }}>Required text sources</label>
          <div style={{ overflowX: 'auto', marginTop: 8 }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
              <thead>
                <tr style={{ textAlign: 'left' }}>
                  <th style={{ padding: '6px 8px', borderBottom: '1px solid #2a3a66' }}>Source</th>
                  <th style={{ padding: '6px 8px', borderBottom: '1px solid #2a3a66', width: 110 }}>Present</th>
                  <th style={{ padding: '6px 8px', borderBottom: '1px solid #2a3a66', width: 120 }}>Input</th>
                  <th style={{ padding: '6px 8px', borderBottom: '1px solid #2a3a66', width: 150 }}>In main scene</th>
                </tr>
              </thead>
              <tbody>
                {(requiredSources || []).map((s) => (
                  <tr key={s.name}>
                    <td style={{ padding: '6px 8px', verticalAlign: 'top' }}>
                      <code style={{ whiteSpace: 'nowrap' }}>{s.name}</code>
                    </td>
                    <td style={{ padding: '6px 8px', verticalAlign: 'top' }}>
                      <span className={s.present ? 'pill pillSuccess' : 'pill pillError'}>{s.present ? 'present' : 'missing'}</span>
                    </td>
                    <td style={{ padding: '6px 8px', verticalAlign: 'top' }}>
                      <span className={s.input_exists ? 'pill pillSuccess' : 'pill pillError'}>{s.input_exists ? 'yes' : 'no'}</span>
                    </td>
                    <td style={{ padding: '6px 8px', verticalAlign: 'top' }}>
                      <span className={s.in_main_scene ? 'pill pillSuccess' : 'pill pillError'}>{s.in_main_scene ? 'yes' : 'no'}</span>
                    </td>
                  </tr>
                ))}
                {!(requiredSources || []).length ? (
                  <tr>
                    <td colSpan={4} className="muted" style={{ padding: '8px 8px' }}>
                      (no data)
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </div>

          <div className="actions" style={{ marginTop: 12 }}>
            <button type="button" onClick={() => loadObsStatus().catch(() => {})} disabled={obsBusy}>
              Refresh OBS status
            </button>

            {hasMissingSources ? (
              <button
                type="button"
                disabled={obsBusy}
                onClick={async () => {
                  setObsBusy(true);
                  setObsEnsureMsg('Creating OBS text sources...');
                  try {
                    const resp = await apiJson<ObsEnsureResp>('/api/obs/ensure_sources', { method: 'POST' });
                    const created = (resp.result?.created || []).length;
                    const added = (resp.result?.added_to_scene || []).length;
                    const errs = resp.result?.errors || {};
                    const errCount = Object.keys(errs).length;
                    const errText = formatObsErrors(errs);
                    const errBlock = errText ? `\n\nErrors:\n${errText}` : '';
                    setObsEnsureMsg(
                      `Created/ensured sources. Created: ${created}, added to scene: ${added}, errors: ${errCount}.${errBlock}\n\nNow go to OBS and set the size + position of each text source.`
                    );
                  } catch (e: any) {
                    setObsEnsureMsg(`Error: ${e?.message ? e.message : String(e)}`);
                  } finally {
                    setObsBusy(false);
                    await loadObsStatus().catch(() => {});
                  }
                }}
              >
                Create missing text sources
              </button>
            ) : null}
          </div>

          {obsMsg ? <div className="muted">{obsMsg}</div> : null}
          {obsEnsureMsg ? <div className="muted" style={{ whiteSpace: 'pre-wrap' }}>{obsEnsureMsg}</div> : null}

          {!obsStatus?.connected ? (
            <div className="muted" style={{ marginTop: 10 }}>
              TipTune couldn’t connect to OBS. Make sure OBS is running, obs-websocket is enabled, and your host/port/password are correct.
            </div>
          ) : null}
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
            value={v('OpenAI', 'model') || 'gpt-5-mini'}
            onChange={(e) => setCfg((c) => ({ ...c, OpenAI: { ...(c.OpenAI || {}), model: e.target.value } }))}
          />
          <div className="muted">Use a supported model name for your account. If you’re unsure, keep the default.</div>
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
          <div className="muted">Tip amount (in tokens) per song request.</div>

          <label>{humanizeKey('skip_song_cost')}</label>
          <input
            type="text"
            value={v('General', 'skip_song_cost')}
            onChange={(e) => setCfg((c) => ({ ...c, General: { ...(c.General || {}), skip_song_cost: e.target.value } }))}
          />
          <div className="muted">Tip amount (in tokens) that triggers a “skip current song” action.</div>

          <label>{humanizeKey('multi_request_tips')}</label>
          <select
            value={(v('General', 'multi_request_tips') || DEFAULT_CFG.General.multi_request_tips).toLowerCase()}
            onChange={(e) => setCfg((c) => ({ ...c, General: { ...(c.General || {}), multi_request_tips: e.target.value } }))}
          >
            <option value="true">true</option>
            <option value="false">false</option>
          </select>
          <div className="muted">If true, multiples of song_cost can request multiple songs in one tip.</div>

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
