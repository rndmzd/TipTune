import { useEffect, useMemo, useState } from 'react';

import { apiJson } from '../api';
import type { Device, QueueState } from '../types';
import { HeaderBar } from '../components/HeaderBar';

declare const __APP_VERSION__: string;

type DevicesResp = { ok: true; devices: Device[] };
type QueueResp = { ok: true; queue: QueueState };
type ConfigResp = { ok: true; config: Record<string, Record<string, string>> };

type SetupStatusResp = {
  ok: true;
  setup_complete: boolean;
  events_configured: boolean;
  openai_configured: boolean;
  google_configured: boolean;
  obs_configured: boolean;
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
  tiptune_audio_capture?: {
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

type ObsScenesResp = { ok: true; scenes: string[] };

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

type ObsEnsureTiptuneAudioResp = {
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
    'Spotify.redirect_url': 'Redirect/callback URL registered in your Spotify app. Must match exactly for authentication to work.',
    'OBS.enabled': 'Enable or disable OBS integration for scene/overlay control.',
    'OBS.host': 'Hostname or IP address where obs-websocket is running (often 127.0.0.1).',
    'OBS.port': 'Port for obs-websocket (often 4455 on newer versions).',
    'OBS.password': 'Password configured for obs-websocket. Leave blank to keep the currently saved password.',
    'Search.google_api_key': 'Google API key used for web search features. Leave blank to keep the currently saved key.',
    'Search.google_cx': 'Google Custom Search Engine (CSE) ID used for web search results.',
    'Music.source': 'Select which music source TipTune will use to fulfill song requests and searches.',
    'General.song_cost': 'Default token cost to request a song.',
    'General.multi_request_tips': 'When enabled, tips that are a multiple of song_cost can request multiple songs. When disabled, only an exact song_cost tip triggers a single request.',
    'General.allow_source_override_in_request_message': 'When enabled, users can include the word “spotify” or “youtube” in their request message to override the selected Music source for that request.',
    'General.skip_song_cost': 'Token cost to skip the currently playing song.',
    'General.request_overlay_duration': 'How long (in seconds) OBS overlays stay visible after they are shown.',
    'General.show_debug_data': 'Show extra debug information in the dashboard (like YouTube playback details).',
    'General.debug_log_to_file': 'When enabled, TipTune writes verbose DEBUG logs to a file. Useful for troubleshooting, but can grow quickly.',
    'General.debug_log_path': 'Optional. Path to the log file. Leave blank to use the app default location.',
  };

  return tips[k] || '';
}

function isTauriRuntime(): boolean {
  const w: any = window as any;
  return !!(w && (w.__TAURI_INTERNALS__ || w.__TAURI__));
}

function stableStringify(value: any): string {
  if (value === null || value === undefined) return 'null';
  if (typeof value !== 'object') return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map((v) => stableStringify(v)).join(',')}]`;
  const keys = Object.keys(value).sort();
  return `{${keys.map((k) => `${JSON.stringify(k)}:${stableStringify(value[k])}`).join(',')}}`;
}

export function SettingsPage() {
  const [currentDeviceText, setCurrentDeviceText] = useState<string>('Loading...');
  const [devices, setDevices] = useState<Device[]>([]);
  const [deviceId, setDeviceId] = useState<string>('');

  const [runtimeAppVersion, setRuntimeAppVersion] = useState<string>('');

  const [cfg, setCfg] = useState<Record<string, Record<string, string>>>({});
  const [baselineCfgSig, setBaselineCfgSig] = useState<string>('');
  const [setupStatus, setSetupStatus] = useState<SetupStatusResp | null>(null);
  const [spotifyStatus, setSpotifyStatus] = useState<SpotifyAuthStatusResp | null>(null);
  const [secrets, setSecrets] = useState({
    eventsUrl: '',
    openaiKey: '',
    googleKey: '',
    obsPassword: '',
  });

  const [status, setStatus] = useState<string>('');

  const [obsStatus, setObsStatus] = useState<ObsStatusResp | null>(null);
  const [obsMsg, setObsMsg] = useState<string>('');
  const [obsEnsureMsg, setObsEnsureMsg] = useState<string>('');
  const [obsSpotifyEnsureMsg, setObsSpotifyEnsureMsg] = useState<string>('');
  const [obsTiptuneEnsureMsg, setObsTiptuneEnsureMsg] = useState<string>('');
  const [obsBusy, setObsBusy] = useState<boolean>(false);

  const [obsScenes, setObsScenes] = useState<string[]>([]);
  const [obsScenesMsg, setObsScenesMsg] = useState<string>('');

  const [updateBusy, setUpdateBusy] = useState<boolean>(false);
  const [updateMsg, setUpdateMsg] = useState<string>('');
  const [updateObj, setUpdateObj] = useState<any>(null);

  async function browseDebugLogPath() {
    if (!isTauriRuntime()) return;
    try {
      const mod: any = await import('@tauri-apps/plugin-dialog');
      const saveFn = mod?.save;
      if (typeof saveFn !== 'function') {
        throw new Error('Dialog API not available.');
      }

      const current = v('General', 'debug_log_path');
      const defaultPath = current && current.trim() ? current.trim() : 'tiptune-debug.log';

      const selected = await saveFn({
        title: 'Choose debug log file',
        defaultPath,
        filters: [{ name: 'Log', extensions: ['log', 'txt'] }],
      });

      if (typeof selected === 'string' && selected.trim()) {
        setCfg((c) => ({ ...c, General: { ...(c.General || {}), debug_log_path: selected } }));
      }
    } catch (e: any) {
      setStatus(`Error: ${e?.message ? e.message : String(e)}`);
    }
  }

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
    const next = data.config || {};
    setCfg(next);
    setBaselineCfgSig(stableStringify(next));
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

  async function loadObsScenes() {
    setObsScenesMsg('Loading scenes...');
    try {
      const host = (v('OBS', 'host') || '').trim();
      const port = (v('OBS', 'port') || '').trim();
      const qs = `?host=${encodeURIComponent(host)}&port=${encodeURIComponent(port)}`;
      const resp = await apiJson<ObsScenesResp>(`/api/obs/scenes${qs}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      });

      const scenes = Array.isArray(resp.scenes) ? resp.scenes.filter((s) => typeof s === 'string' && s.trim() !== '') : [];
      scenes.sort((a, b) => a.localeCompare(b));
      setObsScenes(scenes);
      setObsScenesMsg(scenes.length ? '' : 'No scenes returned from OBS.');
    } catch (e: any) {
      setObsScenes([]);
      setObsScenesMsg(`Error loading scenes: ${e?.message ? e.message : String(e)}`);
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

  useEffect(() => {
    if (!isTauriRuntime()) return;
    let cancelled = false;

    (async () => {
      try {
        const mod: any = await import('@tauri-apps/api/app');
        const getVersionFn = mod?.getVersion;
        if (typeof getVersionFn !== 'function') return;
        const v = await getVersionFn();
        if (cancelled) return;
        if (typeof v === 'string') setRuntimeAppVersion(v);
      } catch {
      }
    })();

    return () => {
      cancelled = true;
    };
  }, []);

  const v = (section: string, key: string) => {
    const value = (cfg[section] || {})[key];
    return value === undefined || value === null ? '' : String(value);
  };

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

  const allowSourceOverrideInRequestMessageEnabled = (() => {
    const raw = v('General', 'allow_source_override_in_request_message');
    const s = (raw || 'true').trim().toLowerCase();
    return !(s === 'false' || s === '0' || s === 'no' || s === 'off');
  })();

  const debugLogToFileEnabled = (() => {
    const raw = v('General', 'debug_log_to_file');
    const s = (raw || 'false').trim().toLowerCase();
    return s === 'true' || s === '1' || s === 'yes' || s === 'y' || s === 'on';
  })();

  const showDebugDataEnabled = (() => {
    const raw = v('General', 'show_debug_data');
    const s = (raw || '').trim().toLowerCase();
    return !(s === 'false' || s === '0' || s === 'no' || s === 'off');
  })();

  const obsEnabled = (v('OBS', 'enabled') || 'false').toLowerCase() === 'true';
  const obsHost = (v('OBS', 'host') || '').trim();
  const obsPort = (v('OBS', 'port') || '').trim();
  const obsSceneName = (v('OBS', 'scene_name') || '').trim();
  const requiredSources = (obsStatus?.status?.sources || []) as ObsSourceStatus[];
  const missingSources = requiredSources.filter((s) => !s.present);
  const hasMissingSources = obsEnabled && !!obsStatus?.enabled && (missingSources.length > 0);

  useEffect(() => {
    if (!obsEnabled) {
      setObsScenes([]);
      setObsScenesMsg('');
      return;
    }
    if (obsHost && obsPort) {
      loadObsScenes().catch(() => {});
    }
  }, [obsEnabled, obsHost, obsPort]);

  const eventsPlaceholder = setupStatus?.events_configured ? '(leave blank to keep)' : '';
  const openaiPlaceholder = setupStatus?.openai_configured ? '(leave blank to keep)' : '';
  const googleKeyPlaceholder = setupStatus?.google_configured ? '(leave blank to keep)' : '';
  const obsPasswordPlaceholder = setupStatus?.obs_configured ? '(leave blank to keep)' : '';

  const spotifyAudio = obsStatus?.spotify_audio_capture || null;
  const tiptuneAudio = obsStatus?.tiptune_audio_capture || null;
  const showCreateSpotifyAudio = obsEnabled && !!obsStatus?.enabled && (spotifyAudio ? !spotifyAudio.present : true);
  const showCreateTiptuneAudio = obsEnabled && !!obsStatus?.enabled && (tiptuneAudio ? !tiptuneAudio.present : true);

  const appVersion = runtimeAppVersion || (typeof __APP_VERSION__ === 'string' ? __APP_VERSION__ : '');

  const isDirty = useMemo(() => {
    if (!baselineCfgSig) return false;
    if (stableStringify(cfg) !== baselineCfgSig) return true;
    return Object.values(secrets).some((s) => String(s || '').trim() !== '');
  }, [baselineCfgSig, cfg, secrets]);

  async function saveSettings() {
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
        redirect_url: v('Spotify', 'redirect_url'),
      },
      Search: {
        google_api_key: secrets.googleKey,
        google_cx: v('Search', 'google_cx'),
      },
      Music: {
        source: v('Music', 'source') || 'spotify',
      },
      General: {
        song_cost: v('General', 'song_cost'),
        multi_request_tips: multiRequestTipsEnabled ? 'true' : 'false',
        allow_source_override_in_request_message: allowSourceOverrideInRequestMessageEnabled ? 'true' : 'false',
        skip_song_cost: v('General', 'skip_song_cost'),
        request_overlay_duration: v('General', 'request_overlay_duration'),
        auto_check_updates: autoCheckUpdatesEnabled ? 'true' : 'false',
        show_debug_data: showDebugDataEnabled ? 'true' : 'false',
        debug_log_to_file: debugLogToFileEnabled ? 'true' : 'false',
        debug_log_path: v('General', 'debug_log_path'),
      },
      OBS: {
        enabled: v('OBS', 'enabled'),
        host: v('OBS', 'host'),
        port: v('OBS', 'port'),
        password: secrets.obsPassword,
        scene_name: v('OBS', 'scene_name'),
      },
    };

    try {
      await apiJson('/api/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });

      setStatus('Saved.');
      setSecrets({ eventsUrl: '', openaiKey: '', googleKey: '', obsPassword: '' });
      await loadConfig();
    } catch (e: any) {
      setStatus(`Error: ${e?.message ? e.message : String(e)}`);
    }
  }

  return (
    <>
      <HeaderBar
        title="Settings"
      />

      <div className="settingsGrid">
        <div className="card">
          <h2>Playback</h2>
          <div className="highlightField">
            <label title={tooltip('Music', 'source')} style={{ marginTop: 0 }}>
              Music source
            </label>
            <select
              title={tooltip('Music', 'source')}
              value={v('Music', 'source') || 'spotify'}
              onChange={(e) => setCfg((c) => ({ ...c, Music: { ...(c.Music || {}), source: e.target.value } }))}
            >
              <option value="spotify">Spotify</option>
              <option value="youtube">YouTube</option>
            </select>
          </div>

          <div style={{ marginTop: 18 }}>
            <label title={tooltip('Playback', 'device_id')} style={{ marginTop: 0 }}>
              Playback device
            </label>
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

        <div className="card">
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

          <label
            title={tooltip('General', 'allow_source_override_in_request_message')}
            style={{ display: 'flex', justifyContent: 'flex-start', width: 'fit-content', gap: 6, alignItems: 'center', marginTop: 12 }}
          >
            <input
              type="checkbox"
              checked={allowSourceOverrideInRequestMessageEnabled}
              style={{ width: 16, height: 16, padding: 0, margin: 0, flex: '0 0 auto' }}
              onChange={(e) =>
                setCfg((c) => ({
                  ...c,
                  General: {
                    ...(c.General || {}),
                    allow_source_override_in_request_message: e.target.checked ? 'true' : 'false',
                  },
                }))
              }
            />
            <span style={{ whiteSpace: 'nowrap' }}>{humanizeKey('allow_source_override_in_request_message')}</span>
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

          <label
            title={tooltip('General', 'show_debug_data')}
            style={{ display: 'flex', justifyContent: 'flex-start', width: 'fit-content', gap: 6, alignItems: 'center', marginTop: 12 }}
          >
            <input
              type="checkbox"
              checked={showDebugDataEnabled}
              style={{ width: 16, height: 16, padding: 0, margin: 0, flex: '0 0 auto' }}
              onChange={(e) =>
                setCfg((c) => ({
                  ...c,
                  General: { ...(c.General || {}), show_debug_data: e.target.checked ? 'true' : 'false' },
                }))
              }
            />
            <span style={{ whiteSpace: 'nowrap' }}>Show debug data in dashboard</span>
          </label>

          <label
            title={tooltip('General', 'debug_log_to_file')}
            style={{ display: 'flex', justifyContent: 'flex-start', width: 'fit-content', gap: 6, alignItems: 'center', marginTop: 12 }}
          >
            <input
              type="checkbox"
              checked={debugLogToFileEnabled}
              style={{ width: 16, height: 16, padding: 0, margin: 0, flex: '0 0 auto' }}
              onChange={(e) =>
                setCfg((c) => ({
                  ...c,
                  General: { ...(c.General || {}), debug_log_to_file: e.target.checked ? 'true' : 'false' },
                }))
              }
            />
            <span style={{ whiteSpace: 'nowrap' }}>Write debug logs to file</span>
          </label>

          <label title={tooltip('General', 'debug_log_path')}>{humanizeKey('debug_log_path')}</label>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <input
              type="text"
              title={tooltip('General', 'debug_log_path')}
              value={v('General', 'debug_log_path')}
              onChange={(e) => setCfg((c) => ({ ...c, General: { ...(c.General || {}), debug_log_path: e.target.value } }))}
              style={{ flex: 1 }}
            />
            {isTauriRuntime() ? (
              <button type="button" onClick={() => browseDebugLogPath().catch(() => {})}>
                Browse…
              </button>
            ) : null}
          </div>
        </div>

        <div className="card">
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

        <div className="card">
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

        <div className="card">
          <h2>Spotify</h2>
          <label title={tooltip('Spotify', 'client_id')}>{humanizeKey('client_id')}</label>
          <input
            type="text"
            title={tooltip('Spotify', 'client_id')}
            value={v('Spotify', 'client_id')}
            onChange={(e) => setCfg((c) => ({ ...c, Spotify: { ...(c.Spotify || {}), client_id: e.target.value } }))}
          />
          <label title={tooltip('Spotify', 'redirect_url')}>{humanizeKey('redirect_url')}</label>
          <input
            type="text"
            title={tooltip('Spotify', 'redirect_url')}
            value={v('Spotify', 'redirect_url')}
            onChange={(e) => setCfg((c) => ({ ...c, Spotify: { ...(c.Spotify || {}), redirect_url: e.target.value } }))}
          />
        </div>

        <div className="card">
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

        <div className="card">
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
            placeholder={obsPasswordPlaceholder}
            title={tooltip('OBS', 'password')}
            value={secrets.obsPassword}
            onChange={(e) => setSecrets((s) => ({ ...s, obsPassword: e.target.value }))}
          />
        </div>

        {obsEnabled ? (
          <div className="card settingsSpanFull">
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

            <div className="actions" style={{ marginTop: 10 }}>
              <button type="button" onClick={() => loadObsScenes().catch(() => {})} disabled={!obsEnabled}>
                Refresh scenes
              </button>
            </div>
            {obsScenesMsg ? <div className="muted">{obsScenesMsg}</div> : null}

            <label>Required text sources</label>
            <div className="tableWrap" style={{ marginTop: 8 }}>
              <table className="dataTable">
                <thead>
                  <tr>
                    <th>Source</th>
                    <th style={{ width: 110 }}>Present</th>
                    <th style={{ width: 120 }}>Input</th>
                    <th style={{ width: 150 }}>In main scene</th>
                  </tr>
                </thead>
                <tbody>
                  {(requiredSources || []).map((s) => (
                    <tr key={s.name}>
                      <td>
                        <code style={{ whiteSpace: 'nowrap' }}>{s.name}</code>
                      </td>
                      <td>
                        <span className={s.present ? 'pill pillSuccess' : 'pill pillError'}>{s.present ? 'present' : 'missing'}</span>
                      </td>
                      <td>
                        <span className={s.input_exists ? 'pill pillSuccess' : 'pill pillError'}>{s.input_exists ? 'yes' : 'no'}</span>
                      </td>
                      <td>
                        <span className={s.in_main_scene ? 'pill pillSuccess' : 'pill pillError'}>{s.in_main_scene ? 'yes' : 'no'}</span>
                      </td>
                    </tr>
                  ))}
                  {!(requiredSources || []).length ? (
                    <tr>
                      <td colSpan={4} className="muted">
                        (no data)
                      </td>
                    </tr>
                  ) : null}
                </tbody>
              </table>
            </div>

            <label style={{ marginTop: 14 }}>TipTune audio capture (YouTube sync)</label>
            <div className="tableWrap" style={{ marginTop: 8 }}>
              <table className="dataTable">
                <thead>
                  <tr>
                    <th>Item</th>
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <td>
                      <code>Application Audio Capture</code>
                    </td>
                    <td>
                      <span className={tiptuneAudio?.present ? 'pill pillSuccess' : 'pill pillError'}>{tiptuneAudio?.present ? 'present' : 'missing'}</span>
                    </td>
                  </tr>
                  <tr>
                    <td className="muted">
                      Target exe
                    </td>
                    <td>{tiptuneAudio?.target_exe || 'TipTune.exe'}</td>
                  </tr>
                  <tr>
                    <td className="muted">
                      Input exists
                    </td>
                    <td>
                      <span className={tiptuneAudio?.input_exists ? 'pill pillSuccess' : 'pill pillError'}>
                        {tiptuneAudio?.input_exists ? 'yes' : 'no'}
                      </span>
                    </td>
                  </tr>
                  <tr>
                    <td className="muted">
                      In main scene
                    </td>
                    <td>
                      <span className={tiptuneAudio?.in_main_scene ? 'pill pillSuccess' : 'pill pillError'}>
                        {tiptuneAudio?.in_main_scene ? 'yes' : 'no'}
                      </span>
                    </td>
                  </tr>
                  <tr>
                    <td className="muted">
                      Matched input
                    </td>
                    <td>
                      {tiptuneAudio?.input_name ? <code>{tiptuneAudio.input_name}</code> : <span className="muted">(none)</span>}
                      {tiptuneAudio?.input_kind ? (
                        <>
                          {' '}<span className="muted">(</span>
                          <code>{tiptuneAudio.input_kind}</code>
                          <span className="muted">)</span>
                        </>
                      ) : null}
                    </td>
                  </tr>
                  {!tiptuneAudio ? (
                    <tr>
                      <td colSpan={2} className="muted">
                        (no data)
                      </td>
                    </tr>
                  ) : null}
                </tbody>
              </table>
            </div>
            {tiptuneAudio?.present ? null : (
              <div className="muted" style={{ marginTop: 8 }}>
                Create an Application Audio Capture input in OBS targeting TipTune.exe to sync YouTube playback audio.
              </div>
            )}

            {showCreateTiptuneAudio ? (
              <div className="actions" style={{ marginTop: 10 }}>
                <button
                  type="button"
                  disabled={obsBusy}
                  onClick={async () => {
                    setObsBusy(true);
                    setObsTiptuneEnsureMsg('Creating TipTune audio capture...');
                    try {
                      const resp = await apiJson<ObsEnsureTiptuneAudioResp>('/api/obs/ensure_tiptune_audio_capture', { method: 'POST' });
                      const r = resp.result || ({} as any);
                      const errs = Array.isArray(r.errors) ? r.errors : [];
                      const errBlock = errs.length ? `\n\nErrors:\n${errs.join('\n')}` : '';

                      setObsTiptuneEnsureMsg(
                        `TipTune audio capture ensured. Created: ${r.created ? 'yes' : 'no'}, configured: ${r.configured ? 'yes' : 'no'}, added to scene: ${r.added_to_scene ? 'yes' : 'no'}.` +
                          (r.input_name ? `\nInput: ${r.input_name}` : '') +
                          (typeof r.targets_exe === 'boolean' ? `\nTargets TipTune.exe: ${r.targets_exe ? 'yes' : 'no'}` : '') +
                          errBlock
                      );
                    } catch (e: any) {
                      setObsTiptuneEnsureMsg(`Error: ${e?.message ? e.message : String(e)}`);
                    } finally {
                      setObsBusy(false);
                      await loadObsStatus().catch(() => {});
                    }
                  }}
                >
                  Create TipTune audio capture
                </button>
              </div>
            ) : null}

            {obsTiptuneEnsureMsg ? <div className="muted" style={{ whiteSpace: 'pre-wrap' }}>{obsTiptuneEnsureMsg}</div> : null}

            <label style={{ marginTop: 14 }}>Spotify audio capture</label>
            <div className="tableWrap" style={{ marginTop: 8 }}>
              <table className="dataTable">
                <thead>
                  <tr>
                    <th>Item</th>
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <td>
                      <code>Application Audio Capture</code>
                    </td>
                    <td>
                      <span className={spotifyAudio?.present ? 'pill pillSuccess' : 'pill pillError'}>{spotifyAudio?.present ? 'present' : 'missing'}</span>
                    </td>
                  </tr>
                  <tr>
                    <td className="muted">
                      Target exe
                    </td>
                    <td>{spotifyAudio?.target_exe || 'Spotify.exe'}</td>
                  </tr>
                  <tr>
                    <td className="muted">
                      Input exists
                    </td>
                    <td>
                      <span className={spotifyAudio?.input_exists ? 'pill pillSuccess' : 'pill pillError'}>
                        {spotifyAudio?.input_exists ? 'yes' : 'no'}
                      </span>
                    </td>
                  </tr>
                  <tr>
                    <td className="muted">
                      In main scene
                    </td>
                    <td>
                      <span className={spotifyAudio?.in_main_scene ? 'pill pillSuccess' : 'pill pillError'}>
                        {spotifyAudio?.in_main_scene ? 'yes' : 'no'}
                      </span>
                    </td>
                  </tr>
                  <tr>
                    <td className="muted">
                      Matched input
                    </td>
                    <td>
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
                      <td colSpan={2} className="muted">
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
        ) : null}

        <div className="card">
          <h2>App Updates</h2>
          <div className="muted" style={{ marginTop: -6 }}>
            Check for updates via GitHub releases.
          </div>

          <div className="muted" style={{ marginTop: 8 }}>
            Current version: {appVersion || '(unknown)'}
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

      {isDirty ? (
        <div className="saveBanner">
          <div className="saveBannerInner">
            <div style={{ fontWeight: 650 }}>Save changes</div>
            <div className="actions" style={{ marginTop: 0 }}>
              <button type="button" onClick={() => saveSettings().catch(() => {})}>
                Save changes
              </button>
              <span className="muted">{status}</span>
            </div>
          </div>
        </div>
      ) : null}
    </>
  );
}
