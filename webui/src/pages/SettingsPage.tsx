import { useEffect, useState } from 'react';

import { apiJson } from '../api';
import type { Device, QueueState } from '../types';
import { HeaderBar } from '../components/HeaderBar';

type DevicesResp = { ok: true; devices: Device[] };
type QueueResp = { ok: true; queue: QueueState };
type ConfigResp = { ok: true; config: Record<string, Record<string, string>> };

type SetupStatusResp = {
  ok: true;
  setup_complete: boolean;
  events_configured: boolean;
  openai_configured: boolean;
  google_configured: boolean;
};

type SpotifyAuthStatusResp = {
  ok: true;
  configured: boolean;
};

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
  spotify_audio_capture?: {
    target_exe?: string;
    input_name?: string | null;
    input_kind?: string | null;
    input_exists?: boolean;
    in_main_scene?: boolean;
    present?: boolean;
  } | null;
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

type ObsEnsureSpotifyAudioResp = {
  ok: true;
  result: {
    scene?: string;
    target_exe?: string;
    input_name?: string | null;
    created?: boolean;
    configured?: boolean;
    added_to_scene?: boolean;
    in_main_scene?: boolean;
    targets_exe?: boolean;
    errors?: string[];
  };
};

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

function tooltip(section: string, key: string) {
  const k = `${section}.${key}`;
  const tips: Record<string, string> = {
    'Playback.device_id': 'Select which Spotify playback device TipTune will use when playing requests. Saved when you click Apply + Save.',
    'Events API.url': 'Endpoint TipTune will call to send event notifications (e.g. request accepted/played). Leave blank to disable events publishing.',
    'Events API.max_requests_per_minute': 'Rate limit for outbound Events API calls to avoid spamming the endpoint.',
    'OpenAI.api_key': 'OpenAI API key used to enable AI features (such as ChatDJ). Leave blank to keep the currently saved key.',
    'OpenAI.model': 'OpenAI model name to use for AI features (for example gpt-5-mini).',
    'Spotify.client_id': 'Spotify application Client ID from your Spotify Developer Dashboard.',
    'Spotify.client_secret': 'Spotify application Client Secret from your Spotify Developer Dashboard. Leave blank to keep the currently saved secret.',
    'Spotify.redirect_url': 'Redirect/callback URL registered in your Spotify app. Must match exactly for authentication to work.',
    'OBS.enabled': 'Enable or disable OBS integration for scene/overlay control.',
    'OBS.host': 'Hostname or IP address where obs-websocket is running (often 127.0.0.1).',
    'OBS.port': 'Port for obs-websocket (often 4455 on newer versions).',
    'OBS.password': 'Password configured for obs-websocket. Leave blank to keep the currently saved password.',
    'Search.google_api_key': 'Google API key used for web search features. Leave blank to keep the currently saved key.',
    'Search.google_cx': 'Google Custom Search Engine (CSE) ID used for web search results.',
    'General.song_cost': 'Default token cost to request a song.',
    'General.multi_request_tips': 'When enabled, tips that are a multiple of song_cost can request multiple songs. When disabled, only an exact song_cost tip triggers a single request.',
    'General.skip_song_cost': 'Token cost to skip the currently playing song.',
    'General.request_overlay_duration': 'How long (in seconds) OBS overlays stay visible after they are shown.',
  };

  return tips[k] || '';
}

function isTauriRuntime(): boolean {
  const w: any = window as any;
  return !!(w && (w.__TAURI_INTERNALS__ || w.__TAURI__));
}

export function SettingsPage() {
  const [currentDeviceText, setCurrentDeviceText] = useState<string>('Loading...');
  const [devices, setDevices] = useState<Device[]>([]);
  const [deviceId, setDeviceId] = useState<string>('');

  const [cfg, setCfg] = useState<Record<string, Record<string, string>>>({});
  const [setupStatus, setSetupStatus] = useState<SetupStatusResp | null>(null);
  const [spotifyStatus, setSpotifyStatus] = useState<SpotifyAuthStatusResp | null>(null);
  const [secrets, setSecrets] = useState({
    eventsUrl: '',
    openaiKey: '',
    spotifySecret: '',
    googleKey: '',
    obsPassword: '',
  });

  const [status, setStatus] = useState<string>('');

  const [obsStatus, setObsStatus] = useState<ObsStatusResp | null>(null);
  const [obsMsg, setObsMsg] = useState<string>('');
  const [obsEnsureMsg, setObsEnsureMsg] = useState<string>('');
  const [obsSpotifyEnsureMsg, setObsSpotifyEnsureMsg] = useState<string>('');
  const [obsBusy, setObsBusy] = useState<boolean>(false);

  const [updateBusy, setUpdateBusy] = useState<boolean>(false);
  const [updateMsg, setUpdateMsg] = useState<string>('');
  const [updateObj, setUpdateObj] = useState<any>(null);

  async function refreshCurrentDevice() {
    const data = await apiJson<QueueResp>('/api/queue');
    const st = data.queue ?? {};
    const devName = st.playback_device_name || '';
    const devId = st.playback_device_id || '';
    setCurrentDeviceText(devId ? `Current: ${devName ? `${devName} ` : ''}(${devId})` : 'Current: (none)');
  }

  async function refreshDevices() {
    const data = await apiJson<DevicesResp>('/api/spotify/devices');
    setDevices(data.devices || []);

    try {
      const qst = await apiJson<QueueResp>('/api/queue');
      const cur = (qst.queue || {}).playback_device_id;
      if (cur) setDeviceId(String(cur));
    } catch {
    }
  }

  async function loadConfig() {
    const data = await apiJson<ConfigResp>('/api/config');
    setCfg(data.config || {});
  }

  async function loadSetupStatus() {
    const data = await apiJson<SetupStatusResp>('/api/setup/status');
    setSetupStatus(data);
  }

  async function loadSpotifyStatus() {
    const data = await apiJson<SpotifyAuthStatusResp>('/api/spotify/auth/status');
    setSpotifyStatus(data);
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

  useEffect(() => {
    Promise.all([
      refreshCurrentDevice().catch((e) => setCurrentDeviceText(`Error: ${e?.message ? e.message : String(e)}`)),
      refreshDevices().catch(() => {}),
      loadConfig().catch((e) => setStatus(`Error loading config: ${e?.message ? e.message : String(e)}`)),
      loadSetupStatus().catch(() => {}),
      loadSpotifyStatus().catch(() => {}),
      loadObsStatus().catch(() => {}),
    ]).catch(() => {});
  }, []);

  const v = (section: string, key: string) => ((cfg[section] || {})[key] || '').toString();

  const autoCheckUpdatesEnabled = (() => {
    const raw = v('General', 'auto_check_updates');
    const s = (raw || 'true').trim().toLowerCase();
    return !(s === 'false' || s === '0' || s === 'no');
  })();

  const multiRequestTipsEnabled = (() => {
    const raw = v('General', 'multi_request_tips');
    const s = (raw || 'true').trim().toLowerCase();
    return !(s === 'false' || s === '0' || s === 'no' || s === 'off');
  })();

  const obsEnabled = (v('OBS', 'enabled') || 'false').toLowerCase() === 'true';
  const requiredSources = (obsStatus?.status?.sources || []) as ObsSourceStatus[];
  const missingSources = requiredSources.filter((s) => !s.present);
  const hasMissingSources = obsEnabled && !!obsStatus?.enabled && (missingSources.length > 0);

  const eventsPlaceholder = setupStatus?.events_configured ? '(leave blank to keep)' : '';
  const openaiPlaceholder = setupStatus?.openai_configured ? '(leave blank to keep)' : '';
  const spotifySecretPlaceholder = spotifyStatus?.configured ? '(leave blank to keep)' : '';
  const googleKeyPlaceholder = setupStatus?.google_configured ? '(leave blank to keep)' : '';

  const spotifyAudio = obsStatus?.spotify_audio_capture || null;
  const showCreateSpotifyAudio = obsEnabled && !!obsStatus?.enabled && (spotifyAudio ? !spotifyAudio.present : true);

  return (
    <>
      <HeaderBar
        title="Settings"
      />

      <div className="row">
        <div className="card">
          <h2>Playback Device</h2>
          <div className="muted">{currentDeviceText}</div>
          <label htmlFor="deviceSelect" title={tooltip('Playback', 'device_id')}>Available devices</label>
          <select id="deviceSelect" value={deviceId} onChange={(e) => setDeviceId(e.target.value)}>
            {(devices || []).map((d, idx) => (
              <option key={idx} value={d.id || ''}>
                {(d.name || '(unknown)') + (d.is_active ? ' (active)' : '')}
              </option>
            ))}
          </select>
          <div className="actions">
            <button type="button" onClick={() => refreshDevices().catch(() => {})}>
              Refresh
            </button>
            <button
              type="button"
              onClick={async () => {
                await apiJson('/api/spotify/device', {
                  method: 'POST',
                  headers: { 'Content-Type': 'application/json' },
                  body: JSON.stringify({ device_id: deviceId, persist: true }),
                });
                await refreshCurrentDevice();
                await refreshDevices();
              }}
            >
              Apply + Save
            </button>
          </div>
        </div>
      </div>

      <div className="row" style={{ marginTop: 16 }}>
        <div className="card" style={{ flex: 1, minWidth: 360 }}>
          <h2>Events API</h2>
          <label title={tooltip('Events API', 'url')}>URL (secret)</label>
          <input
            type="password"
            placeholder={eventsPlaceholder}
            title={tooltip('Events API', 'url')}
            value={secrets.eventsUrl}
            onChange={(e) => setSecrets((s) => ({ ...s, eventsUrl: e.target.value }))}
          />
          <label title={tooltip('Events API', 'max_requests_per_minute')}>{humanizeKey('max_requests_per_minute')}</label>
          <input
            type="text"
            title={tooltip('Events API', 'max_requests_per_minute')}
            value={v('Events API', 'max_requests_per_minute')}
            onChange={(e) => setCfg((c) => ({ ...c, 'Events API': { ...(c['Events API'] || {}), max_requests_per_minute: e.target.value } }))}
          />
        </div>

        <div className="card" style={{ flex: 1, minWidth: 360 }}>
          <h2>OpenAI</h2>
          <label title={tooltip('OpenAI', 'api_key')}>API key (secret)</label>
          <input
            type="password"
            placeholder={openaiPlaceholder}
            title={tooltip('OpenAI', 'api_key')}
            value={secrets.openaiKey}
            onChange={(e) => setSecrets((s) => ({ ...s, openaiKey: e.target.value }))}
          />
          <label title={tooltip('OpenAI', 'model')}>Model</label>
          <input
            type="text"
            title={tooltip('OpenAI', 'model')}
            value={v('OpenAI', 'model')}
            onChange={(e) => setCfg((c) => ({ ...c, OpenAI: { ...(c.OpenAI || {}), model: e.target.value } }))}
          />
        </div>
      </div>

      <div className="row" style={{ marginTop: 16 }}>
        <div className="card" style={{ flex: 1, minWidth: 360 }}>
          <h2>Spotify</h2>
          <label title={tooltip('Spotify', 'client_id')}>{humanizeKey('client_id')}</label>
          <input
            type="text"
            title={tooltip('Spotify', 'client_id')}
            value={v('Spotify', 'client_id')}
            onChange={(e) => setCfg((c) => ({ ...c, Spotify: { ...(c.Spotify || {}), client_id: e.target.value } }))}
          />
          <label title={tooltip('Spotify', 'client_secret')}>{humanizeKey('client_secret')} (secret)</label>
          <input
            type="password"
            placeholder={spotifySecretPlaceholder}
            title={tooltip('Spotify', 'client_secret')}
            value={secrets.spotifySecret}
            onChange={(e) => setSecrets((s) => ({ ...s, spotifySecret: e.target.value }))}
          />
          <label title={tooltip('Spotify', 'redirect_url')}>{humanizeKey('redirect_url')}</label>
          <input
            type="text"
            title={tooltip('Spotify', 'redirect_url')}
            value={v('Spotify', 'redirect_url')}
            onChange={(e) => setCfg((c) => ({ ...c, Spotify: { ...(c.Spotify || {}), redirect_url: e.target.value } }))}
          />
        </div>

        <div className="card" style={{ flex: 1, minWidth: 360 }}>
          <h2>OBS</h2>
          <label title={tooltip('OBS', 'enabled')}>{humanizeKey('enabled')}</label>
          <select
            title={tooltip('OBS', 'enabled')}
            value={(v('OBS', 'enabled') || 'false').toLowerCase()}
            onChange={(e) => setCfg((c) => ({ ...c, OBS: { ...(c.OBS || {}), enabled: e.target.value } }))}
          >
            <option value="true">true</option>
            <option value="false">false</option>
          </select>
          <label title={tooltip('OBS', 'host')}>{humanizeKey('host')}</label>
          <input
            type="text"
            title={tooltip('OBS', 'host')}
            value={v('OBS', 'host')}
            onChange={(e) => setCfg((c) => ({ ...c, OBS: { ...(c.OBS || {}), host: e.target.value } }))}
          />
          <label title={tooltip('OBS', 'port')}>{humanizeKey('port')}</label>
          <input
            type="text"
            title={tooltip('OBS', 'port')}
            value={v('OBS', 'port')}
            onChange={(e) => setCfg((c) => ({ ...c, OBS: { ...(c.OBS || {}), port: e.target.value } }))}
          />
          <label title={tooltip('OBS', 'password')}>{humanizeKey('password')} (secret)</label>
          <input
            type="password"
            placeholder=""
            title={tooltip('OBS', 'password')}
            value={secrets.obsPassword}
            onChange={(e) => setSecrets((s) => ({ ...s, obsPassword: e.target.value }))}
          />
        </div>
      </div>

      <div className="row" style={{ marginTop: 16 }}>
        <div className="card" style={{ flex: 1, minWidth: 360 }}>
          <h2>Search</h2>
          <label title={tooltip('Search', 'google_api_key')}>{humanizeKey('google_api_key')} (secret)</label>
          <input
            type="password"
            placeholder={googleKeyPlaceholder}
            title={tooltip('Search', 'google_api_key')}
            value={secrets.googleKey}
            onChange={(e) => setSecrets((s) => ({ ...s, googleKey: e.target.value }))}
          />
          <label title={tooltip('Search', 'google_cx')}>{humanizeKey('google_cx')}</label>
          <input
            type="text"
            title={tooltip('Search', 'google_cx')}
            value={v('Search', 'google_cx')}
            onChange={(e) => setCfg((c) => ({ ...c, Search: { ...(c.Search || {}), google_cx: e.target.value } }))}
          />
        </div>

        <div className="card" style={{ flex: 1, minWidth: 360 }}>
          <h2>General</h2>
          <label title={tooltip('General', 'song_cost')}>{humanizeKey('song_cost')}</label>
          <input
            type="text"
            title={tooltip('General', 'song_cost')}
            value={v('General', 'song_cost')}
            onChange={(e) => setCfg((c) => ({ ...c, General: { ...(c.General || {}), song_cost: e.target.value } }))}
          />
          <label
            title={tooltip('General', 'multi_request_tips')}
            style={{ display: 'flex', justifyContent: 'flex-start', width: 'fit-content', gap: 6, alignItems: 'center', marginTop: 12 }}
          >
            <input
              type="checkbox"
              checked={multiRequestTipsEnabled}
              style={{ width: 16, height: 16, padding: 0, margin: 0, flex: '0 0 auto' }}
              onChange={(e) =>
                setCfg((c) => ({
                  ...c,
                  General: { ...(c.General || {}), multi_request_tips: e.target.checked ? 'true' : 'false' },
                }))
              }
            />
            <span style={{ whiteSpace: 'nowrap' }}>{humanizeKey('multi_request_tips')}</span>
          </label>
          <label title={tooltip('General', 'skip_song_cost')}>{humanizeKey('skip_song_cost')}</label>
          <input
            type="text"
            title={tooltip('General', 'skip_song_cost')}
            value={v('General', 'skip_song_cost')}
            onChange={(e) => setCfg((c) => ({ ...c, General: { ...(c.General || {}), skip_song_cost: e.target.value } }))}
          />
          <label title={tooltip('General', 'request_overlay_duration')}>OBS overlay duration</label>
          <input
            type="text"
            title={tooltip('General', 'request_overlay_duration')}
            value={v('General', 'request_overlay_duration')}
            onChange={(e) => setCfg((c) => ({ ...c, General: { ...(c.General || {}), request_overlay_duration: e.target.value } }))}
          />
        </div>
      </div>

      {obsEnabled ? (
        <div className="row" style={{ marginTop: 16 }}>
          <div className="card" style={{ flex: 1, minWidth: 360 }}>
            <h2>
              OBS overlay status{' '}
              <span className={obsStatus?.connected ? 'pill pillSuccess' : 'pill pillError'}>{obsStatus?.connected ? 'connected' : 'not connected'}</span>
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

            <label>Required text sources</label>
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

            <label style={{ marginTop: 14 }}>Spotify audio capture</label>
            <div style={{ overflowX: 'auto', marginTop: 8 }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                <thead>
                  <tr style={{ textAlign: 'left' }}>
                    <th style={{ padding: '6px 8px', borderBottom: '1px solid #2a3a66' }}>Item</th>
                    <th style={{ padding: '6px 8px', borderBottom: '1px solid #2a3a66' }}>Status</th>
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <td style={{ padding: '6px 8px', verticalAlign: 'top' }}>
                      <code>Application Audio Capture</code>
                    </td>
                    <td style={{ padding: '6px 8px', verticalAlign: 'top' }}>
                      <span className={spotifyAudio?.present ? 'pill pillSuccess' : 'pill pillError'}>{spotifyAudio?.present ? 'present' : 'missing'}</span>
                    </td>
                  </tr>
                  <tr>
                    <td className="muted" style={{ padding: '6px 8px', verticalAlign: 'top' }}>
                      Target exe
                    </td>
                    <td style={{ padding: '6px 8px', verticalAlign: 'top' }}>{spotifyAudio?.target_exe || 'Spotify.exe'}</td>
                  </tr>
                  <tr>
                    <td className="muted" style={{ padding: '6px 8px', verticalAlign: 'top' }}>
                      Input exists
                    </td>
                    <td style={{ padding: '6px 8px', verticalAlign: 'top' }}>
                      <span className={spotifyAudio?.input_exists ? 'pill pillSuccess' : 'pill pillError'}>
                        {spotifyAudio?.input_exists ? 'yes' : 'no'}
                      </span>
                    </td>
                  </tr>
                  <tr>
                    <td className="muted" style={{ padding: '6px 8px', verticalAlign: 'top' }}>
                      In main scene
                    </td>
                    <td style={{ padding: '6px 8px', verticalAlign: 'top' }}>
                      <span className={spotifyAudio?.in_main_scene ? 'pill pillSuccess' : 'pill pillError'}>
                        {spotifyAudio?.in_main_scene ? 'yes' : 'no'}
                      </span>
                    </td>
                  </tr>
                  <tr>
                    <td className="muted" style={{ padding: '6px 8px', verticalAlign: 'top' }}>
                      Matched input
                    </td>
                    <td style={{ padding: '6px 8px', verticalAlign: 'top' }}>
                      {spotifyAudio?.input_name ? <code>{spotifyAudio.input_name}</code> : <span className="muted">(none)</span>}
                      {spotifyAudio?.input_kind ? (
                        <>
                          {' '}<span className="muted">(</span>
                          <code>{spotifyAudio.input_kind}</code>
                          <span className="muted">)</span>
                        </>
                      ) : null}
                    </td>
                  </tr>
                  {!spotifyAudio ? (
                    <tr>
                      <td colSpan={2} className="muted" style={{ padding: '8px 8px' }}>
                        (no data)
                      </td>
                    </tr>
                  ) : null}
                </tbody>
              </table>
            </div>

            {showCreateSpotifyAudio ? (
              <div className="actions" style={{ marginTop: 10 }}>
                <button
                  type="button"
                  disabled={obsBusy}
                  onClick={async () => {
                    setObsBusy(true);
                    setObsSpotifyEnsureMsg('Creating Spotify audio capture...');
                    try {
                      const resp = await apiJson<ObsEnsureSpotifyAudioResp>('/api/obs/ensure_spotify_audio_capture', { method: 'POST' });
                      const r = resp.result || ({} as any);
                      const errs = Array.isArray(r.errors) ? r.errors : [];
                      const errBlock = errs.length ? `\n\nErrors:\n${errs.join('\n')}` : '';

                      setObsSpotifyEnsureMsg(
                        `Spotify audio capture ensured. Created: ${r.created ? 'yes' : 'no'}, configured: ${r.configured ? 'yes' : 'no'}, added to scene: ${r.added_to_scene ? 'yes' : 'no'}.` +
                          (r.input_name ? `\nInput: ${r.input_name}` : '') +
                          (typeof r.targets_exe === 'boolean' ? `\nTargets Spotify.exe: ${r.targets_exe ? 'yes' : 'no'}` : '') +
                          errBlock
                      );
                    } catch (e: any) {
                      setObsSpotifyEnsureMsg(`Error: ${e?.message ? e.message : String(e)}`);
                    } finally {
                      setObsBusy(false);
                      await loadObsStatus().catch(() => {});
                    }
                  }}
                >
                  Create Spotify audio capture
                </button>
              </div>
            ) : null}

            {obsSpotifyEnsureMsg ? <div className="muted" style={{ whiteSpace: 'pre-wrap' }}>{obsSpotifyEnsureMsg}</div> : null}

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

            <label style={{ marginTop: 14 }}>Test overlays</label>
            <div className="actions">
              <button
                type="button"
                disabled={obsBusy}
                onClick={async () => {
                  setObsBusy(true);
                  setObsEnsureMsg('');
                  try {
                    await apiJson('/api/obs/test_overlay', {
                      method: 'POST',
                      headers: { 'Content-Type': 'application/json' },
                      body: JSON.stringify({ overlay: 'SongRequester' }),
                    });
                    setObsEnsureMsg('Sent test message for SongRequester.');
                  } catch (e: any) {
                    setObsEnsureMsg(`Error: ${e?.message ? e.message : String(e)}`);
                  } finally {
                    setObsBusy(false);
                  }
                }}
              >
                Test SongRequester
              </button>
              <button
                type="button"
                disabled={obsBusy}
                onClick={async () => {
                  setObsBusy(true);
                  setObsEnsureMsg('');
                  try {
                    await apiJson('/api/obs/test_overlay', {
                      method: 'POST',
                      headers: { 'Content-Type': 'application/json' },
                      body: JSON.stringify({ overlay: 'WarningOverlay' }),
                    });
                    setObsEnsureMsg('Sent test message for WarningOverlay.');
                  } catch (e: any) {
                    setObsEnsureMsg(`Error: ${e?.message ? e.message : String(e)}`);
                  } finally {
                    setObsBusy(false);
                  }
                }}
              >
                Test WarningOverlay
              </button>
              <button
                type="button"
                disabled={obsBusy}
                onClick={async () => {
                  setObsBusy(true);
                  setObsEnsureMsg('');
                  try {
                    await apiJson('/api/obs/test_overlay', {
                      method: 'POST',
                      headers: { 'Content-Type': 'application/json' },
                      body: JSON.stringify({ overlay: 'GeneralOverlay' }),
                    });
                    setObsEnsureMsg('Sent test message for GeneralOverlay.');
                  } catch (e: any) {
                    setObsEnsureMsg(`Error: ${e?.message ? e.message : String(e)}`);
                  } finally {
                    setObsBusy(false);
                  }
                }}
              >
                Test GeneralOverlay
              </button>
            </div>
            <div className="muted" style={{ marginTop: 8 }}>
              Note: test overlay duration follows your configured OBS overlay duration setting.
            </div>
          </div>
        </div>
      ) : null}

      <div className="row" style={{ marginTop: 16 }}>
        <div className="card" style={{ flex: 1, minWidth: 360 }}>
          <h2>App Updates</h2>
          <div className="muted" style={{ marginTop: -6 }}>
            Check for updates via GitHub releases.
          </div>

          <label style={{ display: 'flex', justifyContent: 'flex-start', width: 'fit-content', gap: 6, alignItems: 'center', marginTop: 12 }}>
            <input
              type="checkbox"
              checked={autoCheckUpdatesEnabled}
              style={{ width: 16, height: 16, padding: 0, margin: 0, flex: '0 0 auto' }}
              onChange={(e) =>
                setCfg((c) => ({
                  ...c,
                  General: { ...(c.General || {}), auto_check_updates: e.target.checked ? 'true' : 'false' },
                }))
              }
            />
            <span style={{ whiteSpace: 'nowrap' }}>Automatically check for updates</span>
          </label>

          {updateObj ? (
            <div className="muted" style={{ marginTop: 10, whiteSpace: 'pre-wrap' }}>
              Update available.
              {typeof updateObj?.version === 'string' && updateObj.version ? `\nVersion: ${updateObj.version}` : ''}
              {typeof updateObj?.date === 'string' && updateObj.date ? `\nDate: ${updateObj.date}` : ''}
            </div>
          ) : null}

          <div className="actions" style={{ marginTop: 12 }}>
            <button
              type="button"
              disabled={updateBusy}
              onClick={async () => {
                setUpdateBusy(true);
                setUpdateMsg('Checking for updates...');
                setUpdateObj(null);
                try {
                  if (!isTauriRuntime()) {
                    setUpdateMsg('Updater is only available in the desktop app.');
                    return;
                  }

                  const mod: any = await import('@tauri-apps/plugin-updater');
                  const checkFn = mod?.check || mod?.checkUpdate;
                  if (typeof checkFn !== 'function') {
                    throw new Error('Updater API not available.');
                  }

                  const res: any = await checkFn();
                  const update = res && typeof res === 'object' ? res : null;
                  const available =
                    (typeof update?.available === 'boolean' && update.available) ||
                    typeof update?.downloadAndInstall === 'function' ||
                    typeof update?.download === 'function';

                  if (!update || !available) {
                    setUpdateObj(null);
                    setUpdateMsg('No update available.');
                    return;
                  }

                  setUpdateObj(update);
                  const versionLine = typeof update?.version === 'string' && update.version ? `\nVersion: ${update.version}` : '';
                  const dateLine = typeof update?.date === 'string' && update.date ? `\nDate: ${update.date}` : '';

                  if (confirm(`Update available.${versionLine}${dateLine}\n\nDownload + install now?`)) {
                    setUpdateMsg('Downloading update...');
                    if (typeof update.downloadAndInstall === 'function') {
                      await update.downloadAndInstall();
                      setUpdateMsg('Update installed. Restarting...');
                      return;
                    }

                    if (typeof update.download === 'function') {
                      await update.download();
                    }
                    if (typeof update.install === 'function') {
                      await update.install();
                      setUpdateMsg('Update installed. Restarting...');
                      return;
                    }

                    throw new Error('Updater install API not available.');
                  }

                  setUpdateMsg('Update found.');
                } catch (e: any) {
                  setUpdateObj(null);
                  setUpdateMsg(`Error: ${e?.message ? e.message : String(e)}`);
                } finally {
                  setUpdateBusy(false);
                }
              }}
            >
              {updateBusy ? 'Checking…' : 'Check for Updates'}
            </button>

            <button
              type="button"
              disabled={updateBusy || !updateObj}
              onClick={async () => {
                if (!updateObj) return;
                setUpdateBusy(true);
                setUpdateMsg('Downloading update...');
                try {
                  if (typeof updateObj.downloadAndInstall === 'function') {
                    await updateObj.downloadAndInstall();
                    setUpdateMsg('Update installed. Restarting...');
                    return;
                  }

                  if (typeof updateObj.download === 'function') {
                    await updateObj.download();
                  }
                  if (typeof updateObj.install === 'function') {
                    await updateObj.install();
                    setUpdateMsg('Update installed. Restarting...');
                    return;
                  }
                  throw new Error('Updater install API not available.');
                } catch (e: any) {
                  setUpdateMsg(`Error: ${e?.message ? e.message : String(e)}`);
                } finally {
                  setUpdateBusy(false);
                }
              }}
            >
              Download + Install
            </button>

            <span className="muted">{updateMsg}</span>
          </div>
        </div>
      </div>

      <div className="card" style={{ marginTop: 16 }}>
        <div className="actions">
          <button
            type="button"
            onClick={async () => {
              setStatus('Saving...');

              const payload: Record<string, Record<string, string>> = {
                'Events API': {
                  url: secrets.eventsUrl,
                  max_requests_per_minute: v('Events API', 'max_requests_per_minute'),
                },
                OpenAI: {
                  api_key: secrets.openaiKey,
                  model: v('OpenAI', 'model'),
                },
                Spotify: {
                  client_id: v('Spotify', 'client_id'),
                  client_secret: secrets.spotifySecret,
                  redirect_url: v('Spotify', 'redirect_url'),
                },
                Search: {
                  google_api_key: secrets.googleKey,
                  google_cx: v('Search', 'google_cx'),
                },
                General: {
                  song_cost: v('General', 'song_cost'),
                  multi_request_tips: multiRequestTipsEnabled ? 'true' : 'false',
                  skip_song_cost: v('General', 'skip_song_cost'),
                  request_overlay_duration: v('General', 'request_overlay_duration'),
                  auto_check_updates: autoCheckUpdatesEnabled ? 'true' : 'false',
                },
                OBS: {
                  enabled: v('OBS', 'enabled'),
                  host: v('OBS', 'host'),
                  port: v('OBS', 'port'),
                  password: secrets.obsPassword,
                },
              };

              try {
                await apiJson('/api/config', {
                  method: 'POST',
                  headers: { 'Content-Type': 'application/json' },
                  body: JSON.stringify(payload),
                });

                setStatus('Saved.');
                setSecrets({ eventsUrl: '', openaiKey: '', spotifySecret: '', googleKey: '', obsPassword: '' });
                await loadConfig();
              } catch (e: any) {
                setStatus(`Error: ${e?.message ? e.message : String(e)}`);
              }
            }}
          >
            Save Settings
          </button>
          <span className="muted">{status}</span>
        </div>
      </div>
    </>
  );
}
