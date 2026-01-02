import { useEffect, useState } from 'react';

import { apiJson } from '../api';
import type { Device, QueueState } from '../types';
import { HeaderBar } from '../components/HeaderBar';

type DevicesResp = { ok: true; devices: Device[] };
type QueueResp = { ok: true; queue: QueueState };
type ConfigResp = { ok: true; config: Record<string, Record<string, string>> };

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
    'General.skip_song_cost': 'Token cost to skip the currently playing song.',
    'General.request_overlay_duration': 'How long (in seconds) OBS overlays stay visible after they are shown.',
  };

  return tips[k] || '';
}

export function SettingsPage() {
  const [currentDeviceText, setCurrentDeviceText] = useState<string>('Loading...');
  const [devices, setDevices] = useState<Device[]>([]);
  const [deviceId, setDeviceId] = useState<string>('');

  const [cfg, setCfg] = useState<Record<string, Record<string, string>>>({});
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
  const [obsBusy, setObsBusy] = useState<boolean>(false);

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
      loadObsStatus().catch(() => {}),
    ]).catch(() => {});
  }, []);

  const v = (section: string, key: string) => ((cfg[section] || {})[key] || '').toString();

  const obsEnabled = (v('OBS', 'enabled') || 'false').toLowerCase() === 'true';
  const requiredSources = (obsStatus?.status?.sources || []) as ObsSourceStatus[];
  const missingSources = requiredSources.filter((s) => !s.present);
  const hasMissingSources = obsEnabled && !!obsStatus?.enabled && (missingSources.length > 0);

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
            placeholder="(leave blank to keep)"
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
            placeholder="(leave blank to keep)"
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
            placeholder="(leave blank to keep)"
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
            placeholder="(leave blank to keep)"
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
            placeholder="(leave blank to keep)"
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
              <span className="pill">{obsStatus?.connected ? 'connected' : 'not connected'}</span>
            </h2>

            <div className="muted">
              Current scene: <code>{obsStatus?.status?.current_scene || '(unknown)'}</code>
            </div>

            <label>Required text sources</label>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginTop: 8 }}>
              {(requiredSources || []).map((s) => (
                <div key={s.name} style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                  <code style={{ minWidth: 160 }}>{s.name}</code>
                  <span className="pill">{s.present ? 'present' : 'missing'}</span>
                  <span className="muted">input: {s.input_exists ? 'yes' : 'no'}</span>
                  <span className="muted">in main scene: {s.in_main_scene ? 'yes' : 'no'}</span>
                </div>
              ))}
              {!(requiredSources || []).length ? <div className="muted">(no data)</div> : null}
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

                      setObsEnsureMsg(
                        `Created/ensured sources. Created: ${created}, added to scene: ${added}, errors: ${errCount}.\n\nNow go to OBS and set the size + position of each text source.`
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
                  skip_song_cost: v('General', 'skip_song_cost'),
                  request_overlay_duration: v('General', 'request_overlay_duration'),
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
