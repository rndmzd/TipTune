import { useEffect, useMemo, useRef, useState } from 'react';

import { apiJson } from '../api';
import type { Device, ObsStatus, QueueState } from '../types';
import { HeaderBar } from '../components/HeaderBar';
import { OverlaySettings } from '../components/OverlaySettings';
import { ObsSetup } from '../components/ObsSetup';
import { ObsAudioSettings } from '../components/ObsAudioSettings';
import { ConfiguredSection } from '../components/ConfiguredSection';
import { SettingsLayout } from '../components/SettingsLayout';
import { refreshReportingConsent, setReportingConsent, type ReportingStatus } from '../errorReporting';

declare const __APP_VERSION__: string;

type DevicesResp = { ok: true; devices: Device[]; error?: string };
type QueueResp = { ok: true; queue: QueueState };
type ConfigResp = { ok: true; config: Record<string, Record<string, string>> };

function historyDebugEnabled(raw: unknown): boolean {
  return ['true', '1', 'yes', 'on'].includes(String(raw ?? '').trim().toLowerCase());
}

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
  authorized: boolean;
  client_ready: boolean;
  in_progress: boolean;
  error?: string | null;
};

type ObsScenesResp = { ok: true; scenes: string[] };

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
    'OpenAI.model': 'OpenAI model for song requests. Blank uses gpt-6-luna; gpt-6-sol and gpt-6-astra are also supported. Requires Responses Structured Outputs and API account access.',
    'Spotify.client_id': 'Spotify application Client ID from your Spotify Developer Dashboard.',
    'Spotify.redirect_url': 'Redirect/callback URL registered in your Spotify app. Must match exactly for authentication to work.',
    'OBS.enabled': 'Enable or disable OBS integration for scene/overlay control.',
    'OBS.host': 'Hostname or IP address where obs-websocket is running (often 127.0.0.1).',
    'OBS.port': 'Port for obs-websocket (often 4455 on newer versions).',
    'OBS.password': 'Password configured for obs-websocket. Leave blank to keep the currently saved password.',
    'Search.google_api_key': 'Google API key used for web search features. Leave blank to keep the currently saved key.',
    'Search.google_cx': 'Google Custom Search Engine (CSE) ID used for web search results.',
    'Music.source': 'Select which music source TipTune will use to fulfill song requests and searches.',
    'General.song_cost': 'Exact token amount required to request one song.',
    'General.allow_source_override_in_request_message': 'When enabled, users can include the word “spotify” or “youtube” in their request message to override the selected Music source for that request.',
    'General.skip_song_cost': 'Token cost to skip the currently playing song.',
    'General.request_overlay_duration': 'How long (in seconds) OBS overlays stay visible after they are shown.',
    'General.request_history_size': 'Maximum number of song request results to save. Default: 1,000 entries.',
    'General.show_debug_data': 'Show extra debug information in the dashboard (like YouTube playback details).',
    'General.show_history_debug': 'Show saved song extraction diagnostics on failed History entries. Details are saved even when this display is off. Default: off.',
    'General.send_error_reports': 'Opt in to automatically sending technical error reports to the TipTune developer through Sentry. Default: off.',
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

export function SettingsPage({ audioOutputError = '', audioOutputActive }: { audioOutputError?: string; audioOutputActive?: boolean | null }) {
  const [currentDeviceText, setCurrentDeviceText] = useState<string>('Loading...');
  const [devices, setDevices] = useState<Device[]>([]);
  const [deviceId, setDeviceId] = useState<string>('');
  const manualDeviceId = useRef('');
  const [devicesBusy, setDevicesBusy] = useState(false);
  const [devicesError, setDevicesError] = useState('');
  const [devicesLoaded, setDevicesLoaded] = useState(false);
  const [deviceApplyBusy, setDeviceApplyBusy] = useState(false);
  const [deviceApplyMsg, setDeviceApplyMsg] = useState('');
  const [spotifyAuthBusy, setSpotifyAuthBusy] = useState(false);
  const [spotifyAuthMsg, setSpotifyAuthMsg] = useState('');

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
  const [saveBusy, setSaveBusy] = useState<boolean>(false);
  const [reportingStatus, setReportingStatus] = useState<ReportingStatus | null>(null);
  const [reportingBusy, setReportingBusy] = useState(false);
  const [reportingMessage, setReportingMessage] = useState('');

  const [obsStatus, setObsStatus] = useState<ObsStatus | null>(null);
  const [obsMsg, setObsMsg] = useState<string>('');
  const obsStatusRequest = useRef(0);
  const obsStatusMounted = useRef(true);

  const [obsScenes, setObsScenes] = useState<string[]>([]);
  const [obsScenesMsg, setObsScenesMsg] = useState<string>('');
  const [obsScenesBusy, setObsScenesBusy] = useState(false);
  const obsScenesRequest = useRef(0);

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

  function showCurrentDevice(st: QueueState) {
    const devName = st.playback_device_name || '';
    const devId = st.playback_device_id || '';
    setCurrentDeviceText(devId ? `Current: ${devName ? `${devName} ` : ''}(${devId})` : 'Current: (none)');
  }

  async function refreshCurrentDevice() {
    const data = await apiJson<QueueResp>('/api/queue');
    showCurrentDevice(data.queue ?? {});
  }

  async function refreshDevices() {
    setDevicesBusy(true);
    setDevicesError('');
    try {
      const data = await apiJson<DevicesResp>('/api/spotify/devices');
      // Also accept the older backend's ok:true + error response during upgrades.
      if (data.error) throw new Error(data.error);
      const next = Array.isArray(data.devices) ? data.devices : [];
      setDevices(next);
      let current = '';
      let currentExplicit = false;
      try {
        const qst = await apiJson<QueueResp>('/api/queue');
        current = String(qst.queue?.playback_device_id || '');
        currentExplicit = qst.queue?.playback_device_explicit === true;
        showCurrentDevice(qst.queue ?? {});
      } catch {}
      const usable = next.filter((d) => d.id?.trim() && !d.is_restricted);
      const embedded = usable.filter((d) => d.name?.trim().toLowerCase() === 'web player (microsoft edge)');
      const defaultId = embedded.find((d) => d.id === current)?.id
        || embedded.find((d) => d.is_active)?.id || embedded[0]?.id;
      setDeviceId((previous) => {
        if (usable.some((d) => d.id === manualDeviceId.current)) return manualDeviceId.current;
        if (currentExplicit && usable.some((d) => d.id === current)) return current;
        if (defaultId) return defaultId;
        if (usable.some((d) => d.id === previous)) return previous;
        if (usable.some((d) => d.id === current)) return current;
        return usable.find((d) => d.is_active)?.id || usable[0]?.id || '';
      });
    } catch (e: any) {
      setDevices([]);
      setDeviceId('');
      setDevicesError(e?.message || String(e));
    } finally {
      setDevicesLoaded(true);
      setDevicesBusy(false);
      loadSpotifyStatus().catch(() => {});
    }
  }

  async function loadConfig(expectedHistoryDebug?: boolean, expectedSendReports?: boolean) {
    const data = await apiJson<ConfigResp>('/api/config', { cache: 'no-store' });
    const next = data.config || {};
    if (expectedHistoryDebug !== undefined && historyDebugEnabled(next.General?.show_history_debug) !== expectedHistoryDebug) {
      throw new Error('The history debug setting did not persist. Your changes are still shown; try saving again.');
    }
    if (expectedSendReports !== undefined && historyDebugEnabled(next.General?.send_error_reports) !== expectedSendReports) {
      throw new Error('The error reporting setting did not persist. Your changes are still shown; try saving again.');
    }
    setCfg(next);
    setBaselineCfgSig(stableStringify(next));
    setReportingStatus(await refreshReportingConsent());
  }

  async function sendTestReport() {
    setReportingBusy(true);
    setReportingMessage('');
    try {
      const response = await apiJson<{ ok: true; report_id: string }>('/api/error-reporting/test', { method: 'POST' });
      setReportingMessage(`Test report queued. Report ID: ${response.report_id}`);
      setReportingStatus(await refreshReportingConsent());
    } catch (error: any) {
      setReportingMessage(error?.message || 'The test report could not be queued.');
    } finally {
      setReportingBusy(false);
    }
  }

  async function loadSetupStatus() {
    const data = await apiJson<SetupStatusResp>('/api/setup/status');
    setSetupStatus(data);
  }

  async function loadSpotifyStatus() {
    const data = await apiJson<SpotifyAuthStatusResp>('/api/spotify/auth/status');
    setSpotifyStatus(data);
  }

  async function connectSpotify() {
    setSpotifyAuthBusy(true);
    setSpotifyAuthMsg('Starting Spotify authorization…');
    try {
      const data = await apiJson<{ ok: true; auth_url: string }>('/api/spotify/auth/start', { method: 'POST' });
      if (isTauriRuntime()) {
        const { open } = await import('@tauri-apps/plugin-shell');
        await open(data.auth_url);
      } else {
        window.open(data.auth_url, '_blank', 'noopener,noreferrer');
      }
      setSpotifyAuthMsg('Complete authorization in your browser with the same account used in the Spotify player.');
    } catch (e: any) {
      setSpotifyAuthMsg(`Error: ${e?.message || String(e)}`);
    } finally {
      setSpotifyAuthBusy(false);
      loadSpotifyStatus().catch(() => {});
    }
  }

  useEffect(() => {
    if (!spotifyStatus?.in_progress) return;
    let cancelled = false;
    const timer = window.setInterval(async () => {
      try {
        const data = await apiJson<SpotifyAuthStatusResp>('/api/spotify/auth/status');
        if (cancelled) return;
        setSpotifyStatus(data);
        if (!data.in_progress) {
          setSpotifyAuthMsg(data.error ? `Error: ${data.error}` : data.client_ready ? 'Spotify API connected.' : 'Spotify authorization did not complete. Try connecting again.');
          await refreshDevices();
        }
      } catch (e: any) {
        if (!cancelled) setSpotifyAuthMsg(`Error: ${e?.message || String(e)}`);
      }
    }, 2000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [spotifyStatus?.in_progress]);

  async function loadObsStatus() {
    const request = ++obsStatusRequest.current;
    try {
      const data = await apiJson<ObsStatus>('/api/obs/status', undefined, 20000);
      if (!obsStatusMounted.current || request !== obsStatusRequest.current) return;
      setObsStatus(data);
      setObsMsg('');
    } catch (e: any) {
      if (!obsStatusMounted.current || request !== obsStatusRequest.current) return;
      setObsStatus(null);
      setObsMsg(`Error loading OBS status: ${e?.message ? e.message : String(e)}`);
    }
  }

  async function loadObsScenes() {
    const request = ++obsScenesRequest.current;
    setObsScenesBusy(true);
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

      if (request !== obsScenesRequest.current) return;
      const scenes = Array.isArray(resp.scenes) ? resp.scenes.filter((s) => typeof s === 'string' && s.trim() !== '') : [];
      scenes.sort((a, b) => a.localeCompare(b));
      setObsScenes(scenes);
      setObsScenesMsg(scenes.length ? '' : 'No scenes returned from OBS.');
    } catch (e: any) {
      if (request !== obsScenesRequest.current) return;
      setObsScenes([]);
      setObsScenesMsg(`Error loading scenes: ${e?.message ? e.message : String(e)}`);
    } finally {
      if (request === obsScenesRequest.current) setObsScenesBusy(false);
    }
  }

  useEffect(() => {
    Promise.all([
      refreshCurrentDevice().catch((e) => setCurrentDeviceText(`Error: ${e?.message ? e.message : String(e)}`)),
      refreshDevices().catch(() => {}),
      loadConfig().catch((e) => setStatus(`Error loading config: ${e?.message ? e.message : String(e)}`)),
      loadSetupStatus().catch(() => {}),
      loadSpotifyStatus().catch(() => {}),
    ]).catch(() => {});
  }, []);

  useEffect(() => {
    obsStatusMounted.current = true;
    let stopped = false;
    let timer: number | undefined;
    async function poll() {
      await loadObsStatus();
      if (!stopped) timer = window.setTimeout(poll, 5000);
    }
    poll();
    return () => {
      stopped = true;
      obsStatusMounted.current = false;
      ++obsStatusRequest.current;
      if (timer) window.clearTimeout(timer);
    };
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
  const showHistoryDebugEnabled = historyDebugEnabled(v('General', 'show_history_debug'));
  const sendErrorReportsEnabled = historyDebugEnabled(v('General', 'send_error_reports'));
  const obsHost = (v('OBS', 'host') || '').trim();
  const obsPort = (v('OBS', 'port') || '').trim();
  const obsSceneName = (v('OBS', 'scene_name') || '').trim();
  const obsConnected = obsEnabled && !!obsStatus?.connected;

  useEffect(() => {
    if (!obsConnected) {
      ++obsScenesRequest.current;
      setObsScenes([]);
      setObsScenesMsg('');
      setObsScenesBusy(false);
      return;
    }
    if (obsHost && obsPort) {
      loadObsScenes().catch(() => {});
    }
    return () => { ++obsScenesRequest.current; };
  }, [obsConnected, obsHost, obsPort]);

  const eventsPlaceholder = setupStatus?.events_configured ? '(leave blank to keep)' : '';
  const openaiPlaceholder = setupStatus?.openai_configured ? '(leave blank to keep)' : '';
  const googleKeyPlaceholder = setupStatus?.google_configured ? '(leave blank to keep)' : '';
  const obsPasswordPlaceholder = setupStatus?.obs_configured ? '(leave blank to keep)' : '';

  const appVersion = runtimeAppVersion || (typeof __APP_VERSION__ === 'string' ? __APP_VERSION__ : '');

  const isDirty = useMemo(() => {
    if (!baselineCfgSig) return false;
    if (stableStringify(cfg) !== baselineCfgSig) return true;
    return Object.values(secrets).some((s) => String(s || '').trim() !== '');
  }, [baselineCfgSig, cfg, secrets]);

  const savedCfg = useMemo<Record<string, Record<string, string>>>(() => baselineCfgSig ? JSON.parse(baselineCfgSig) : {}, [baselineCfgSig]);
  function sectionChanged(section: string, secret = '', keys?: string[]) {
    if (!baselineCfgSig) return false;
    const changed = keys ? keys.some((key) => (cfg[section]?.[key] || '') !== (savedCfg[section]?.[key] || ''))
      : stableStringify(cfg[section] || {}) !== stableStringify(savedCfg[section] || {});
    return changed || !!secret.trim();
  }
  const spotifySettingsDirty = sectionChanged('Spotify');
  const spotifyConnected = !!spotifyStatus?.client_ready && !!spotifyStatus?.authorized && !spotifyStatus?.error;
  const obsConnectionDirty = sectionChanged('OBS', secrets.obsPassword, ['enabled', 'host', 'port']);
  const obsSetupDirty = sectionChanged('OBS', '', ['scene_name']) || sectionChanged('Overlay', '', ['source_name']);
  const savedObsPort = Number(savedCfg.OBS?.port);
  const obsConnectionReady = !obsEnabled || obsConnected || (!!savedCfg.OBS?.host?.trim() && Number.isInteger(savedObsPort) && savedObsPort > 0 && savedObsPort <= 65535);
  const obsWaiting = obsEnabled && !!obsStatus && !obsConnected && !obsMsg;
  const obsConnectionLabel = !obsEnabled ? 'Disabled' : obsMsg ? 'Status unavailable' : !obsStatus ? 'Checking OBS…'
    : obsConnected ? 'Connected' : obsConnectionReady ? 'Waiting for OBS' : 'Needs setup';

  async function saveSettings() {
    if (saveBusy) return;
    const historySizeRaw = cfg.General?.request_history_size ?? '1000';
    const historySize = Number(historySizeRaw);
    if (!/^\d+$/.test(historySizeRaw.trim()) || !Number.isSafeInteger(historySize) || historySize < 1) {
      setStatus('Error: Request history size must be a positive whole number.');
      return;
    }
    setSaveBusy(true);
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
        allow_source_override_in_request_message: allowSourceOverrideInRequestMessageEnabled ? 'true' : 'false',
        skip_song_cost: v('General', 'skip_song_cost'),
        request_overlay_duration: v('General', 'request_overlay_duration'),
        request_history_size: String(historySize),
        auto_check_updates: autoCheckUpdatesEnabled ? 'true' : 'false',
        show_debug_data: showDebugDataEnabled ? 'true' : 'false',
        show_history_debug: showHistoryDebugEnabled ? 'true' : 'false',
        send_error_reports: sendErrorReportsEnabled ? 'true' : 'false',
        debug_log_to_file: debugLogToFileEnabled ? 'true' : 'false',
        debug_log_path: v('General', 'debug_log_path'),
      },
      OBS: {
        enabled: v('OBS', 'enabled'),
        host: v('OBS', 'host'),
        port: v('OBS', 'port'),
        password: secrets.obsPassword,
        scene_name: v('OBS', 'scene_name'),
        use_virtual_cable: historyDebugEnabled(v('OBS', 'use_virtual_cable')) ? 'true' : 'false',
      },
      Overlay: cfg.Overlay || {},
    };

    try {
      await apiJson('/api/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });

      if (!sendErrorReportsEnabled) setReportingConsent(false);
      await loadConfig(showHistoryDebugEnabled, sendErrorReportsEnabled);
      setSecrets({ eventsUrl: '', openaiKey: '', googleKey: '', obsPassword: '' });
      setStatus('Saved.');
      await Promise.all([refreshDevices(), loadSetupStatus().catch(() => {}), loadSpotifyStatus().catch(() => {}), loadObsStatus()]);
    } catch (e: any) {
      setStatus(`Error: ${e?.message ? e.message : String(e)}`);
    } finally {
      setSaveBusy(false);
    }
  }

  const sceneControls = obsEnabled ? (
    <div className="overlay-scenes" role="group" aria-labelledby="overlay-scenes-heading" aria-busy={obsScenesBusy}>
      <h3 id="overlay-scenes-heading">OBS scenes</h3>
      {obsConnected ? <dl className="overlay-scene-status">
        <dt>Current scene</dt>
        <dd><code>{obsStatus?.status?.current_scene || '(unknown)'}</code></dd>
        <dt>Main scene</dt>
        <dd><code>{obsStatus?.status?.main_scene || '(unknown)'}</code></dd>
      </dl> : null}
      <label htmlFor="obs-overlay-scene">Overlay scene</label>
      <select id="obs-overlay-scene" value={obsSceneName} disabled={!obsConnected}
        aria-describedby={obsScenesMsg ? 'obs-scenes-feedback' : undefined}
        onChange={(e) => setCfg((c) => ({ ...c, OBS: { ...(c.OBS || {}), scene_name: e.target.value } }))}>
        <option value="">(select a scene)</option>
        {Array.from(new Set([obsSceneName, ...obsScenes].filter(Boolean))).map((scene) => (
          <option key={scene} value={scene}>{scene}</option>
        ))}
      </select>
      {obsConnected ? <div className="actions">
        <button type="button" onClick={() => loadObsScenes()} disabled={obsScenesBusy}>Refresh scenes</button>
      </div> : null}
      {obsConnected && obsScenesMsg ? <p id="obs-scenes-feedback" className="muted" role="status">{obsScenesMsg}</p> : null}
    </div>
  ) : null;

  const settingsSections = useMemo(() => [
    { id: 'settings-playback', label: 'Playback' },
    { id: 'settings-general', label: 'General' },
    { id: 'settings-logging', label: 'Logging' },
    { id: 'settings-events', label: 'Events API' },
    { id: 'settings-openai', label: 'OpenAI' },
    { id: 'settings-spotify', label: 'Spotify' },
    { id: 'settings-search', label: 'Search' },
    { id: 'settings-obs', label: 'OBS connection' },
    { id: 'settings-obs-setup', label: 'Add to OBS' },
    { id: 'settings-display', label: 'Stream information display' },
    ...(obsEnabled ? [{ id: 'settings-obs-status', label: 'OBS audio sources' }] : []),
    { id: 'settings-updates', label: 'App Updates' },
  ], [obsEnabled]);

  return (
    <SettingsLayout sections={settingsSections} header={
      <>
        {isDirty || status ? (
          <div className="saveBanner">
            <div className="saveBannerInner">
              <div className="saveBannerMessage" role="status" aria-live="polite">
                {isDirty ? <strong>Unsaved changes</strong> : null}
                {isDirty && !saveBusy ? <span className="muted">Your changes won’t take effect until you save.</span> : null}
                {status && (!isDirty || status !== 'Saved.') ? <span className="muted">{status}</span> : null}
              </div>
              {isDirty ? (
                <button
                  className="saveBannerButton"
                  type="button"
                  disabled={saveBusy}
                  onClick={() => saveSettings().catch(() => {})}
                >
                  {saveBusy ? 'Saving…' : 'Save changes'}
                </button>
              ) : null}
            </div>
          </div>
        ) : null}
        <HeaderBar title="Settings" />
      </>
    }>
        <section id="settings-playback" className="card settingsSection" aria-labelledby="settings-playback-heading" tabIndex={-1}>
          <h2 id="settings-playback-heading">Playback</h2>
          <div className="highlightField">
            <label htmlFor="music-source" title={tooltip('Music', 'source')} style={{ marginTop: 0 }}>
              Music source
            </label>
            <select
              id="music-source"
              title={tooltip('Music', 'source')}
              value={v('Music', 'source') || 'spotify'}
              onChange={(e) => setCfg((c) => ({ ...c, Music: { ...(c.Music || {}), source: e.target.value } }))}
            >
              <option value="spotify">Spotify</option>
              <option value="youtube">YouTube</option>
            </select>
            <label className="settingsCheckbox" title={tooltip('General', 'allow_source_override_in_request_message')}>
              <input
                type="checkbox"
                checked={allowSourceOverrideInRequestMessageEnabled}
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
              <span>{humanizeKey('allow_source_override_in_request_message')}</span>
            </label>
          </div>

          <div style={{ marginTop: 18 }}>
            <label title={tooltip('Playback', 'device_id')} style={{ marginTop: 0 }}>
              Playback device
            </label>
            <div className="muted">{currentDeviceText}</div>
            <label htmlFor="deviceSelect" title={tooltip('Playback', 'device_id')}>Available devices</label>
            <select id="deviceSelect" value={deviceId} disabled={devicesBusy || !devices.length} onChange={(e) => { manualDeviceId.current = e.target.value; setDeviceId(e.target.value); setDeviceApplyMsg(''); }}>
              {!devices.length ? <option value="">{devicesBusy ? 'Loading devices…' : devicesError ? 'Devices unavailable' : 'No devices reported by Spotify'}</option> : null}
              {(devices || []).map((d, idx) => (
                <option key={d.id || idx} value={d.id || ''} disabled={!d.id || d.is_restricted}>
                  {(d.name || '(unknown)') + (d.is_active ? ' (active)' : '') + (d.is_restricted ? ' (restricted)' : '')}
                </option>
              ))}
            </select>
            <div className="actions">
              <button type="button" disabled={devicesBusy} onClick={() => refreshDevices()}>
                {devicesBusy ? 'Refreshing…' : 'Refresh'}
              </button>
              <button
                type="button"
                disabled={devicesBusy || deviceApplyBusy || !devices.some((d) => d.id === deviceId && !d.is_restricted && d.id)}
                onClick={async () => {
                  setDeviceApplyBusy(true);
                  setDeviceApplyMsg('');
                  try {
                    await apiJson('/api/spotify/device', {
                      method: 'POST', headers: { 'Content-Type': 'application/json' },
                      body: JSON.stringify({ device_id: deviceId, persist: true }),
                    });
                    await refreshCurrentDevice();
                    await refreshDevices();
                    setDeviceApplyMsg('Playback device saved.');
                  } catch (e: any) { setDeviceApplyMsg(`Error: ${e?.message || String(e)}`); }
                  finally { setDeviceApplyBusy(false); }
                }}
              >
                {deviceApplyBusy ? 'Applying…' : 'Apply + Save'}
              </button>
            </div>
            {devicesError ? <p role="alert">{devicesError}</p> : devicesLoaded && !devices.length && !devicesBusy ? <p className="muted" role="status">Spotify returned no devices. Check that the player uses the same account you authorized for TipTune, start playback, then refresh.</p> : null}
            {deviceApplyMsg ? <p role="status">{deviceApplyMsg}</p> : null}
            <p className="muted">Web Player (Microsoft Edge) is selected and activated automatically when available. Use Apply + Save to choose another device for this session.</p>
            <p className="muted">The Spotify player login and TipTune API authorization are separate. Connect Spotify below to allow TipTune to discover and control devices.</p>
          </div>
        </section>

        <section id="settings-general" className="card settingsSection" aria-labelledby="settings-general-heading" tabIndex={-1}>
          <h2 id="settings-general-heading">General</h2>

          <label htmlFor="request-history-size" title={tooltip('General', 'request_history_size')}>Request history size</label>
          <input
            id="request-history-size"
            type="number"
            min={1}
            step={1}
            value={cfg.General?.request_history_size ?? '1000'}
            aria-describedby="request-history-size-help"
            onChange={(e) => setCfg((c) => ({ ...c, General: { ...(c.General || {}), request_history_size: e.target.value } }))}
          />
          <p id="request-history-size-help" className="muted">
            Maximum saved request entries (default: 1,000). Lowering this removes the oldest entries when you save.
          </p>

          <label htmlFor="song-cost" title={tooltip('General', 'song_cost')}>{humanizeKey('song_cost')}</label>
          <input
            id="song-cost"
            type="text"
            title={tooltip('General', 'song_cost')}
            value={v('General', 'song_cost')}
            onChange={(e) => setCfg((c) => ({ ...c, General: { ...(c.General || {}), song_cost: e.target.value } }))}
          />
          <label htmlFor="skip-song-cost" title={tooltip('General', 'skip_song_cost')}>{humanizeKey('skip_song_cost')}</label>
          <input
            id="skip-song-cost"
            type="text"
            title={tooltip('General', 'skip_song_cost')}
            value={v('General', 'skip_song_cost')}
            onChange={(e) => setCfg((c) => ({ ...c, General: { ...(c.General || {}), skip_song_cost: e.target.value } }))}
          />
        </section>

        <section id="settings-logging" className="card settingsSection" aria-labelledby="settings-logging-heading" tabIndex={-1}>
          <h2 id="settings-logging-heading">Logging</h2>
          <label className="settingsCheckbox" title={tooltip('General', 'show_debug_data')}>
            <input
              type="checkbox"
              checked={showDebugDataEnabled}
              onChange={(e) =>
                setCfg((c) => ({
                  ...c,
                  General: { ...(c.General || {}), show_debug_data: e.target.checked ? 'true' : 'false' },
                }))
              }
            />
            <span>Show debug data in dashboard</span>
          </label>

          <label className="settingsCheckbox" title={tooltip('General', 'debug_log_to_file')}>
            <input
              type="checkbox"
              checked={debugLogToFileEnabled}
              onChange={(e) =>
                setCfg((c) => ({
                  ...c,
                  General: { ...(c.General || {}), debug_log_to_file: e.target.checked ? 'true' : 'false' },
                }))
              }
            />
            <span>Write debug logs to file</span>
          </label>

          <label htmlFor="debug-log-path" title={tooltip('General', 'debug_log_path')}>{humanizeKey('debug_log_path')}</label>
          <div className="settingsFieldRow">
            <input
              id="debug-log-path"
              type="text"
              title={tooltip('General', 'debug_log_path')}
              value={v('General', 'debug_log_path')}
              onChange={(e) => setCfg((c) => ({ ...c, General: { ...(c.General || {}), debug_log_path: e.target.value } }))}
            />
            {isTauriRuntime() ? (
              <button type="button" onClick={() => browseDebugLogPath().catch(() => {})}>
                Browse…
              </button>
            ) : null}
          </div>

          <label className="settingsCheckbox" title={tooltip('General', 'show_history_debug')}>
            <input
              type="checkbox"
              checked={showHistoryDebugEnabled}
              aria-describedby="history-debug-help"
              onChange={(e) => {
                const value = e.currentTarget.checked ? 'true' : 'false';
                setCfg((c) => ({
                  ...c, General: { ...(c.General || {}), show_history_debug: value },
                }));
              }}
            />
            <span>Show debug output in history</span>
          </label>
          <p id="history-debug-help" className="muted">
            Show extraction errors, model input and responses, and timing on failed requests. Diagnostics are saved even when hidden.
          </p>

          <label className="settingsCheckbox" title={tooltip('General', 'send_error_reports')}>
            <input
              type="checkbox"
              checked={sendErrorReportsEnabled}
              disabled={!reportingStatus?.configured}
              aria-describedby="error-reporting-help"
              onChange={(e) => {
                const value = e.currentTarget.checked ? 'true' : 'false';
                setCfg((c) => ({ ...c, General: { ...(c.General || {}), send_error_reports: value } }));
              }}
            />
            <span>Automatically send error reports to the TipTune developer</span>
          </label>
          <p id="error-reporting-help" className="muted">
            Off by default. Reports go through Sentry and include the app version, operating system,
            exception types, code locations, and technical failure details. Usernames, tip messages,
            credentials, and raw error text are excluded. Turning this off and saving deletes unsent reports.
          </p>
          {reportingStatus?.configured === false && <p className="muted">Error reporting is unavailable in this build.</p>}
          {sendErrorReportsEnabled && isDirty && <p className="muted">Save your changes before sending a test report.</p>}
          <button type="button" disabled={reportingBusy || isDirty || !sendErrorReportsEnabled || !reportingStatus?.enabled || !reportingStatus?.configured}
            onClick={sendTestReport}>{reportingBusy ? 'Queuing test…' : 'Send test report'}</button>
          {reportingMessage && <p role="status">{reportingMessage}</p>}
          {reportingStatus?.error && <p role="status">{reportingStatus.error}</p>}
        </section>

        <ConfiguredSection id="settings-events" title="Events API" ready={!!setupStatus?.events_configured}
          status={!setupStatus ? 'Checking…' : setupStatus.events_configured ? 'Configured' : 'Needs setup'}
          tone={setupStatus?.events_configured ? 'success' : 'neutral'} pending={sectionChanged('Events API', secrets.eventsUrl)}>
          <label htmlFor="events-url" title={tooltip('Events API', 'url')}>URL (secret)</label>
          <input
            id="events-url"
            type="password"
            placeholder={eventsPlaceholder}
            title={tooltip('Events API', 'url')}
            value={secrets.eventsUrl}
            onChange={(e) => setSecrets((s) => ({ ...s, eventsUrl: e.target.value }))}
          />
          <label htmlFor="events-max-requests" title={tooltip('Events API', 'max_requests_per_minute')}>{humanizeKey('max_requests_per_minute')}</label>
          <input
            id="events-max-requests"
            type="text"
            title={tooltip('Events API', 'max_requests_per_minute')}
            value={v('Events API', 'max_requests_per_minute')}
            onChange={(e) => setCfg((c) => ({ ...c, 'Events API': { ...(c['Events API'] || {}), max_requests_per_minute: e.target.value } }))}
          />
        </ConfiguredSection>

        <ConfiguredSection id="settings-openai" title="OpenAI" ready={!!setupStatus?.openai_configured}
          status={!setupStatus ? 'Checking…' : setupStatus.openai_configured ? 'Configured' : 'Needs setup'}
          tone={setupStatus?.openai_configured ? 'success' : 'neutral'} pending={sectionChanged('OpenAI', secrets.openaiKey)}>
          <label htmlFor="openai-api-key" title={tooltip('OpenAI', 'api_key')}>API key (secret)</label>
          <input
            id="openai-api-key"
            type="password"
            placeholder={openaiPlaceholder}
            title={tooltip('OpenAI', 'api_key')}
            value={secrets.openaiKey}
            onChange={(e) => setSecrets((s) => ({ ...s, openaiKey: e.target.value }))}
          />
          <label htmlFor="openai-model" title={tooltip('OpenAI', 'model')}>Model</label>
          <input
            id="openai-model"
            type="text"
            title={tooltip('OpenAI', 'model')}
            value={v('OpenAI', 'model')}
            onChange={(e) => setCfg((c) => ({ ...c, OpenAI: { ...(c.OpenAI || {}), model: e.target.value } }))}
          />
        </ConfiguredSection>

        <ConfiguredSection id="settings-spotify" title="Spotify" ready={spotifyConnected}
          status={!spotifyStatus ? 'Checking…' : spotifyConnected ? 'Connected' : spotifyStatus.in_progress ? 'Connecting…' : spotifyStatus.configured ? spotifyStatus.authorized ? 'Reconnect needed' : 'Authorization needed' : 'Needs setup'}
          tone={spotifyConnected ? 'success' : 'neutral'} pending={spotifySettingsDirty}
          actions={<button type="button" disabled={spotifyAuthBusy || spotifySettingsDirty || !spotifyStatus?.configured}
            onClick={() => connectSpotify()}>{spotifyAuthBusy ? 'Connecting…' : spotifyStatus?.authorized ? 'Reconnect Spotify' : 'Connect Spotify'}</button>}
          feedback={<>
            {spotifyStatus?.error ? <p className="settingsDisclosureFeedback" role="alert">{spotifyStatus.error}</p> : null}
            {spotifyStatus?.in_progress ? <p className="settingsDisclosureFeedback" role="status">Waiting for Spotify authorization…</p> : null}
            {spotifySettingsDirty ? <p className="settingsDisclosureFeedback muted">Save changes before connecting Spotify.</p> : null}
            {spotifyAuthMsg ? <p className="settingsDisclosureFeedback" role="status">{spotifyAuthMsg}</p> : null}
          </>}>
          <label htmlFor="spotify-client-id" title={tooltip('Spotify', 'client_id')}>{humanizeKey('client_id')}</label>
          <input
            id="spotify-client-id"
            type="text"
            title={tooltip('Spotify', 'client_id')}
            value={v('Spotify', 'client_id')}
            onChange={(e) => setCfg((c) => ({ ...c, Spotify: { ...(c.Spotify || {}), client_id: e.target.value } }))}
          />
          <label htmlFor="spotify-redirect-url" title={tooltip('Spotify', 'redirect_url')}>{humanizeKey('redirect_url')}</label>
          <input
            id="spotify-redirect-url"
            type="text"
            title={tooltip('Spotify', 'redirect_url')}
            value={v('Spotify', 'redirect_url')}
            onChange={(e) => setCfg((c) => ({ ...c, Spotify: { ...(c.Spotify || {}), redirect_url: e.target.value } }))}
          />
        </ConfiguredSection>

        <ConfiguredSection id="settings-search" title="Search" ready={!!setupStatus?.google_configured}
          status={!setupStatus ? 'Checking…' : setupStatus.google_configured ? 'Configured' : 'Needs setup'}
          tone={setupStatus?.google_configured ? 'success' : 'neutral'} pending={sectionChanged('Search', secrets.googleKey)}>
          <label htmlFor="search-api-key" title={tooltip('Search', 'google_api_key')}>{humanizeKey('google_api_key')} (secret)</label>
          <input
            id="search-api-key"
            type="password"
            placeholder={googleKeyPlaceholder}
            title={tooltip('Search', 'google_api_key')}
            value={secrets.googleKey}
            onChange={(e) => setSecrets((s) => ({ ...s, googleKey: e.target.value }))}
          />
          <label htmlFor="search-engine-id" title={tooltip('Search', 'google_cx')}>{humanizeKey('google_cx')}</label>
          <input
            id="search-engine-id"
            type="text"
            title={tooltip('Search', 'google_cx')}
            value={v('Search', 'google_cx')}
            onChange={(e) => setCfg((c) => ({ ...c, Search: { ...(c.Search || {}), google_cx: e.target.value } }))}
          />
        </ConfiguredSection>

        <ConfiguredSection id="settings-obs" title="OBS connection" ready={obsConnectionReady}
          status={obsConnectionLabel} tone={obsConnected ? 'success' : 'neutral'} pending={obsConnectionDirty}
          feedback={obsMsg ? <p className="settingsDisclosureFeedback muted" role="status">{obsMsg}</p>
            : obsWaiting ? <p className="settingsDisclosureFeedback muted">Open OBS and enable its WebSocket server. If OBS is already open, check the connection settings.</p> : undefined}>
          <label htmlFor="obs-enabled" title={tooltip('OBS', 'enabled')}>OBS connection (automatic setup)</label>
          <select
            id="obs-enabled"
            title={tooltip('OBS', 'enabled')}
            value={(v('OBS', 'enabled') || 'false').toLowerCase()}
            onChange={(e) => setCfg((c) => ({ ...c, OBS: { ...(c.OBS || {}), enabled: e.target.value } }))}
          >
            <option value="true">true</option>
            <option value="false">false</option>
          </select>
          <label htmlFor="obs-host" title={tooltip('OBS', 'host')}>{humanizeKey('host')}</label>
          <input
            id="obs-host"
            type="text"
            title={tooltip('OBS', 'host')}
            value={v('OBS', 'host')}
            onChange={(e) => setCfg((c) => ({ ...c, OBS: { ...(c.OBS || {}), host: e.target.value } }))}
          />
          <label htmlFor="obs-port" title={tooltip('OBS', 'port')}>{humanizeKey('port')}</label>
          <input
            id="obs-port"
            type="text"
            title={tooltip('OBS', 'port')}
            value={v('OBS', 'port')}
            onChange={(e) => setCfg((c) => ({ ...c, OBS: { ...(c.OBS || {}), port: e.target.value } }))}
          />
          <label htmlFor="obs-password" title={tooltip('OBS', 'password')}>{humanizeKey('password')} (secret)</label>
          <input
            id="obs-password"
            type="password"
            placeholder={obsPasswordPlaceholder}
            title={tooltip('OBS', 'password')}
            value={secrets.obsPassword}
            onChange={(e) => setSecrets((s) => ({ ...s, obsPassword: e.target.value }))}
          />
        </ConfiguredSection>

        <ObsSetup cfg={cfg} status={obsStatus} statusError={obsMsg} dirty={isDirty} pending={obsSetupDirty}
          sceneControls={sceneControls} onRefreshStatus={loadObsStatus}
          onChange={(section, key, value) => setCfg((c) => ({ ...c, [section]: { ...(c[section] || {}), [key]: value } }))} />

        <section id="settings-display" className="card settingsSection overlay-settings-card" aria-labelledby="settings-display-heading" tabIndex={-1}>
          <OverlaySettings cfg={cfg} dirty={isDirty} status={obsStatus}
            onChange={(section, key, value) => setCfg((c) => ({ ...c, [section]: { ...(c[section] || {}), [key]: value } }))} />
        </section>

        {obsEnabled ? <ObsAudioSettings status={obsStatus} error={obsMsg} audioOutputError={audioOutputError} outputActive={audioOutputActive} dirty={isDirty}
          enabled={historyDebugEnabled(v('OBS', 'use_virtual_cable'))} pending={sectionChanged('OBS', '', ['use_virtual_cable'])}
          onEnabledChange={(enabled) => setCfg((c) => ({ ...c, OBS: { ...(c.OBS || {}), use_virtual_cable: enabled ? 'true' : 'false' } }))}
          onRefresh={loadObsStatus} /> : null}

        <section id="settings-updates" className="card settingsSection" aria-labelledby="settings-updates-heading" tabIndex={-1}>
          <h2 id="settings-updates-heading">App Updates</h2>
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
            <span>Automatically check for updates</span>
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
        </section>
    </SettingsLayout>
  );
}
