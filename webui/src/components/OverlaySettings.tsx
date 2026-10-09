import { useEffect, useState } from 'react';
import { apiJson } from '../api';
import { OverlayCanvas } from '../overlay/OverlayCanvas';
import { overlayDefaults, sampleState, type OverlayAlert } from '../overlay/types';
import { useOverlay } from '../overlay/useOverlay';
import './overlay-settings.css';

type Config = Record<string, Record<string, string>>;
type DisplayStatus = {
  overlay_url?: string; display_mode?: string; display_clients?: number; connected?: boolean;
  viewport?: { width: number; height: number };
  browser_source?: { configured?: boolean; error?: string } | null;
};

export function OverlaySettings({ cfg, onChange, dirty = false, onActivated }: {
  cfg: Config; onChange: (section: string, key: string, value: string) => void;
  dirty?: boolean; onActivated?: () => void;
}) {
  const settings = { ...overlayDefaults, ...cfg.Overlay };
  const [previewMode, setPreviewMode] = useState('sample');
  const [framing, setFraming] = useState('canvas');
  const live = useOverlay(previewMode === 'live');
  const [alerts, setAlerts] = useState<OverlayAlert[]>([]);
  const [status, setStatus] = useState<DisplayStatus>({});
  const [scenes, setScenes] = useState<string[]>([]);
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const connectionEnabled = cfg.OBS?.enabled === 'true';
  const update = (key: string, value: string) => onChange('Overlay', key, value);
  async function refresh() {
    try { setStatus(await apiJson<DisplayStatus>('/api/obs/status', undefined, 20000)); }
    catch (error: any) { setMessage(error?.message || 'Cannot reach TipTune. Restart the app and refresh status.'); }
  }
  async function refreshScenes() {
    try {
      const response = await apiJson<{ scenes: string[] }>('/api/obs/scenes', { method: 'POST' }, 20000);
      setScenes(response.scenes || []);
    } catch (error: any) { setMessage(error?.message || 'Cannot reach OBS. Check its WebSocket settings.'); }
  }
  useEffect(() => {
    let stopped = false;
    let timer: number | undefined;
    async function poll() {
      try {
        const next = await apiJson<DisplayStatus>('/api/obs/status', undefined, 20000);
        if (!stopped) setStatus(next);
      } catch { /* The live preview exposes the connection state. */ }
      if (!stopped) timer = window.setTimeout(poll, 5000);
    }
    poll();
    return () => { stopped = true; if (timer) window.clearTimeout(timer); };
  }, []);
  useEffect(() => {
    if (connectionEnabled) refreshScenes();
  }, [connectionEnabled]);
  useEffect(() => {
    const timer = window.setInterval(() => setAlerts((previous) => {
      const active = previous.filter((a) => a.expires_at > Date.now() / 1000);
      return active.length === previous.length ? previous : active;
    }), 250);
    return () => window.clearInterval(timer);
  }, []);
  function sample(kind: string) {
    setPreviewMode('sample');
    const now = Date.now() / 1000;
    const text = { request: 'M83 — Midnight City', warning: 'Couldn’t find that song. Include an artist and title.', general: 'Song request queue resumed' };
    setAlerts((previous) => [...previous.slice(-2), {
      id: `${now}-${kind}`, kind, message: text[kind as keyof typeof text],
      requester: kind === 'general' ? null : 'StarGazer', created_at: now, expires_at: now + 10,
    }]);
  }
  async function addBrowser() {
    setBusy(true); setMessage('Adding the browser overlay…');
    try {
      const response = await apiJson<{ result: { legacy_errors?: string[] } }>('/api/obs/ensure_browser_source', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ scene_name: cfg.OBS?.scene_name, source_name: settings.source_name, activate: true }),
      }, 45000);
      update('mode', 'browser');
      setMessage(response.result.legacy_errors?.length
        ? `Browser overlay added. Hide these old text sources in OBS: ${response.result.legacy_errors.join('; ')}`
        : 'Browser overlay added. Existing text inputs are retained; their items in this scene are hidden.');
      onActivated?.();
      await refresh();
    } catch (error: any) { setMessage(error?.message || 'Could not add the browser source. Refresh status and try again.'); }
    finally { setBusy(false); }
  }
  async function sendTest(overlay: string) {
    setBusy(true);
    try {
      await apiJson('/api/obs/test_overlay', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ overlay }),
      });
      setMessage('Test sent to the actual overlay.');
    } catch (error: any) { setMessage(error?.message || 'Save Browser display mode before testing.'); }
    finally { setBusy(false); }
  }
  const sampleData = sampleState(); sampleData.alerts = alerts;
  const url = status.overlay_url || 'http://127.0.0.1:8765/overlay';
  const dimensions = status.viewport || { width: 1920, height: 1080 };
  return <div className="overlay-settings">
    <h2>Stream information display</h2>
    <p className="muted">One transparent browser source for songs, the queue, and request notices. Sound uses your existing audio capture.</p>
    <label htmlFor="overlay-mode">Info display</label>
    <select id="overlay-mode" value={settings.mode} onChange={(e) => update('mode', e.target.value)}>
      <option value="browser">Browser display</option><option value="text">Legacy text sources</option><option value="off">Off</option>
    </select>
    <div className="overlay-controls">
      <div><label htmlFor="overlay-layout">Layout</label><select id="overlay-layout" value={settings.layout} onChange={(e) => update('layout', e.target.value)}><option value="full">Full</option><option value="compact">Compact</option></select></div>
      <div><label htmlFor="overlay-position">Position</label><select id="overlay-position" value={settings.position} onChange={(e) => update('position', e.target.value)}>{['bottom-left', 'bottom-right', 'top-left', 'top-right'].map((p) => <option key={p} value={p}>{p.replace('-', ' ')}</option>)}</select></div>
      {([['scale', 'Scale (%)', 50, 150], ['margin', 'Edge margin', 0, 200], ['queue_length', 'Upcoming songs', 1, 5], ['opacity', 'Panel opacity (%)', 0, 100]] as const).map(([key, label, min, max]) => <div key={key}><label htmlFor={`overlay-${key}`}>{label}</label><input id={`overlay-${key}`} type="number" min={min} max={max} value={settings[key]} onChange={(e) => update(key, e.target.value)} /></div>)}
      {([['accent_color', 'Accent color'], ['background_color', 'Background color'], ['text_color', 'Text color']] as const).map(([key, label]) => <div key={key}><label htmlFor={`overlay-${key}`}>{label}</label><input id={`overlay-${key}`} type="color" value={settings[key]} onChange={(e) => update(key, e.target.value)} /></div>)}
      <div><label htmlFor="overlay-motion">Motion</label><select id="overlay-motion" value={settings.motion} onChange={(e) => update('motion', e.target.value)}><option value="subtle">Subtle</option><option value="off">Off</option></select></div>
    </div>
    <div className="overlay-toggles">{([['show_now_playing', 'Now Playing'], ['show_queue', 'Queue'], ['show_alerts', 'Alerts'], ['show_artwork', 'Artwork']] as const).map(([key, label]) => <label key={key}><input type="checkbox" checked={settings[key] !== 'false'} onChange={(e) => update(key, String(e.target.checked))} />{label}</label>)}</div>
    <div className="overlay-preview-toolbar"><strong>Preview</strong><select aria-label="Preview content" value={previewMode} onChange={(e) => setPreviewMode(e.target.value)}><option value="sample">Sample</option><option value="live">Live</option></select><span className="muted">{previewMode === 'sample' ? 'Illustrative songs; preview only' : live.connected ? 'Display connected' : live.stale ? 'Waiting for TipTune' : 'Reconnecting…'}</span></div>
    <label htmlFor="overlay-preview-framing">Preview framing</label>
    <select id="overlay-preview-framing" value={framing} onChange={(e) => setFraming(e.target.value)}>
      <option value="canvas">Full canvas — check position and scale</option>
      <option value="inspect">Inspect panels — readable, scrollable detail</option>
    </select>
    {framing === 'inspect' ? <p className="muted">Scroll to inspect song, requester, and alert text. This view uses a readable size and does not change your broadcast scale or position.</p> : null}
    <div className={`overlay-preview${framing === 'inspect' ? ' overlay-preview-inspect' : ''}`}><OverlayCanvas state={previewMode === 'sample' ? sampleData : live.state} config={settings} preview inspect={framing === 'inspect'} /></div>
    <div className="actions"><button type="button" onClick={() => sample('request')}>Preview request</button><button type="button" onClick={() => sample('warning')}>Preview warning</button><button type="button" onClick={() => sample('general')}>Preview notice</button></div>
    <div className="overlay-setup">
      <h3>Add to OBS</h3>
      <div className="overlay-status"><span>OBS: {status.connected ? 'connected' : 'not connected'}</span><span>Browser source: {status.browser_source?.configured ? 'configured' : 'not configured'}</span><span>Display clients: {status.display_clients || 0}</span></div>
      {status.browser_source?.error ? <p role="status">{status.browser_source.error}</p> : null}
      <label htmlFor="overlay-url">Browser source URL</label><div className="overlay-copy"><input id="overlay-url" value={url} readOnly /><button type="button" onClick={async () => { try { await navigator.clipboard.writeText(url); setMessage('URL copied.'); } catch { setMessage('Select the URL and copy it manually.'); } }}>Copy URL</button></div>
      <p className="muted">In OBS, add a Browser source with this URL. Set width {dimensions.width}, height {dimensions.height}, and 30 FPS. Keep both shutdown and scene-activation refresh options off. Start TipTune first.</p>
      <label htmlFor="overlay-source-name">OBS source name</label><input id="overlay-source-name" maxLength={100} value={settings.source_name} onChange={(e) => update('source_name', e.target.value)} />
      {connectionEnabled ? <><label htmlFor="browser-overlay-scene">OBS scene</label><select id="browser-overlay-scene" value={cfg.OBS?.scene_name || ''} onChange={(e) => onChange('OBS', 'scene_name', e.target.value)}><option value="">Select a scene</option>{Array.from(new Set([cfg.OBS?.scene_name, ...scenes].filter(Boolean))).map((s) => <option key={s} value={s}>{s}</option>)}</select><div className="actions"><button type="button" disabled={busy} onClick={() => refreshScenes()}>Refresh scenes</button><button type="button" disabled={busy || dirty || !cfg.OBS?.scene_name} onClick={addBrowser}>{busy ? 'Working…' : status.display_mode === 'browser' ? 'Add browser overlay' : 'Switch to browser overlay'}</button></div>{dirty ? <p className="muted">Save your changes before adding the source to OBS.</p> : null}</> : <p className="muted">Enable the OBS connection for automatic source setup. Manual browser display works without it.</p>}
      <div className="actions"><button type="button" disabled={busy} onClick={() => refresh()}>Refresh status</button><button type="button" disabled={busy || dirty || settings.mode !== 'browser'} onClick={() => sendTest('SongRequester')}>Send request to OBS</button><button type="button" disabled={busy || dirty || settings.mode !== 'browser'} onClick={() => sendTest('WarningOverlay')}>Send warning to OBS</button><button type="button" disabled={busy || dirty || settings.mode !== 'browser'} onClick={() => sendTest('GeneralOverlay')}>Send notice to OBS</button></div>
      <p className="muted">Send buttons appear on your actual overlay. Preview buttons affect only this preview.</p>
      {message ? <p className="overlay-feedback" role="status">{message}</p> : null}
    </div>
  </div>;
}
