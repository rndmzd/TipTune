import { useEffect, useState } from 'react';
import type { ObsStatus } from '../types';
import { apiJson } from '../api';
import { OverlayCanvas } from '../overlay/OverlayCanvas';
import { overlayDefaults, requestInfoFromConfig, sampleState, type OverlayAdvertisement, type OverlayAlert } from '../overlay/types';
import { useOverlay } from '../overlay/useOverlay';
import './overlay-settings.css';

type Config = Record<string, Record<string, string>>;
export function OverlaySettings({ cfg, onChange, dirty = false, status }: {
  cfg: Config; onChange: (section: string, key: string, value: string) => void;
  dirty?: boolean; status: ObsStatus | null;
}) {
  const settings = { ...overlayDefaults, ...cfg.Overlay };
  const [previewMode, setPreviewMode] = useState('sample');
  const [framing, setFraming] = useState('canvas');
  const live = useOverlay(previewMode === 'live');
  const [alerts, setAlerts] = useState<OverlayAlert[]>([]);
  const [advertisement, setAdvertisement] = useState<OverlayAdvertisement | null>(null);
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const displayEnabled = settings.mode === 'browser';
  const canSend = displayEnabled && (!!status?.browser_source?.configured && !!status?.connected || (status?.display_clients || 0) > 0);
  const update = (key: string, value: string) => onChange('Overlay', key, value);
  useEffect(() => {
    const timer = window.setInterval(() => {
      const now = Date.now() / 1000;
      setAlerts((previous) => {
        const active = previous.filter((a) => a.expires_at > now);
        return active.length === previous.length ? previous : active;
      });
      setAdvertisement((previous) => previous && previous.expires_at <= now ? null : previous);
    }, 250);
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
  function previewAdvertisement() {
    setPreviewMode('sample');
    const now = Date.now() / 1000;
    const duration = Number(settings.advertisement_duration);
    setAdvertisement({ id: `preview-ad-${now}`, created_at: now,
      expires_at: now + (Number.isFinite(duration) ? Math.max(5, Math.min(30, duration)) : 15) });
  }
  async function sendTest(overlay: string) {
    setBusy(true);
    try {
      await apiJson('/api/obs/test_overlay', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ overlay }),
      });
      setMessage('Test sent to the actual overlay.');
    } catch (error: any) { setMessage(error?.message || 'Enable and save the stream information display before testing.'); }
    finally { setBusy(false); }
  }
  const sampleData = sampleState(); sampleData.alerts = alerts;
  sampleData.advertisement = advertisement;
  sampleData.request_info = requestInfoFromConfig(cfg.General);
  const previewSettings = previewMode === 'sample' && advertisement
    ? { ...settings, show_advertisements: 'true' } : settings;
  return <div className="overlay-settings">
    <h2 id="settings-display-heading">Stream information display</h2>
    <p className="muted">One transparent browser source for songs, the queue, and request notices. Sound uses your existing audio capture.</p>
    <div className="overlay-toggles"><label htmlFor="overlay-enabled">
      <input id="overlay-enabled" type="checkbox" checked={displayEnabled}
        onChange={(e) => update('mode', e.target.checked ? 'browser' : 'off')} />
      Show stream information display
    </label></div>
    <label htmlFor="request-overlay-duration">OBS overlay duration (seconds)</label>
    <input id="request-overlay-duration" type="text" inputMode="decimal"
      value={cfg.General?.request_overlay_duration || ''} aria-describedby="request-overlay-duration-help"
      onChange={(e) => onChange('General', 'request_overlay_duration', e.target.value)} />
    <p id="request-overlay-duration-help" className="muted">How long request, warning, and notice overlays stay visible. Advertisement duration is set separately below.</p>
    <div className="overlay-controls">
      <div><label htmlFor="overlay-layout">Layout</label><select id="overlay-layout" value={settings.layout} onChange={(e) => update('layout', e.target.value)}><option value="full">Full</option><option value="compact">Compact</option></select></div>
      <div><label htmlFor="overlay-position">Position</label><select id="overlay-position" value={settings.position} onChange={(e) => update('position', e.target.value)}>{['bottom-left', 'bottom-right', 'top-left', 'top-right'].map((p) => <option key={p} value={p}>{p.replace('-', ' ')}</option>)}</select></div>
      {([['scale', 'Scale (%)', 50, 150], ['margin', 'Edge margin', 0, 200], ['queue_length', 'Upcoming songs', 1, 5], ['opacity', 'Panel opacity (%)', 0, 100]] as const).map(([key, label, min, max]) => <div key={key}><label htmlFor={`overlay-${key}`}>{label}</label><input id={`overlay-${key}`} type="number" min={min} max={max} value={settings[key]} onChange={(e) => update(key, e.target.value)} /></div>)}
      {([['accent_color', 'Accent color'], ['background_color', 'Background color'], ['text_color', 'Text color']] as const).map(([key, label]) => <div key={key}><label htmlFor={`overlay-${key}`}>{label}</label><input id={`overlay-${key}`} type="color" value={settings[key]} onChange={(e) => update(key, e.target.value)} /></div>)}
      <div><label htmlFor="overlay-motion">Motion</label><select id="overlay-motion" value={settings.motion} onChange={(e) => update('motion', e.target.value)}><option value="subtle">Subtle</option><option value="off">Off</option></select></div>
    </div>
    <div className="overlay-toggles">{([['show_now_playing', 'Now Playing'], ['show_queue', 'Queue'], ['show_alerts', 'Alerts'], ['show_artwork', 'Artwork']] as const).map(([key, label]) => <label key={key}><input type="checkbox" checked={settings[key] !== 'false'} onChange={(e) => update(key, String(e.target.checked))} />{label}</label>)}</div>
    <section className="overlay-advertisements" aria-labelledby="overlay-advertisements-heading">
      <h3 id="overlay-advertisements-heading">Advertisements</h3>
      <div className="overlay-toggles"><label><input type="checkbox" checked={settings.show_advertisements === 'true'} onChange={(e) => update('show_advertisements', String(e.target.checked))} />Show song request advertisements</label></div>
      <p className="muted">Periodically show the song price, how to request a song, and the skip price on the browser display. Prices follow your General settings.</p>
      <div className="overlay-controls">
        <div><label htmlFor="overlay-advertisement-interval">Ad interval (seconds)</label><input id="overlay-advertisement-interval" type="number" min={60} max={86400} step={1} disabled={settings.show_advertisements !== 'true'} value={settings.advertisement_interval} onChange={(e) => update('advertisement_interval', e.target.value)} /></div>
        <div><label htmlFor="overlay-advertisement-duration">Ad duration (seconds)</label><input id="overlay-advertisement-duration" type="number" min={5} max={30} step={1} disabled={settings.show_advertisements !== 'true'} value={settings.advertisement_duration} onChange={(e) => update('advertisement_duration', e.target.value)} /></div>
      </div>
      <p className="muted">The first ad appears after one interval, then repeats from the start of each ad. Save changes to apply. Motion follows the display’s Motion setting.</p>
      {settings.show_advertisements === 'true' && settings.mode !== 'browser' ? <p role="status">Enable the stream information display to show advertisements on your overlay.</p> : null}
    </section>
    <div className="overlay-preview-toolbar"><strong>Preview</strong><select aria-label="Preview content" value={previewMode} onChange={(e) => setPreviewMode(e.target.value)}><option value="sample">Sample</option><option value="live">Live</option></select><span className="muted">{previewMode === 'sample' ? 'Illustrative songs; preview only' : live.connected ? 'Display connected' : live.stale ? 'Waiting for TipTune' : 'Reconnecting…'}</span></div>
    <label htmlFor="overlay-preview-framing">Preview framing</label>
    <select id="overlay-preview-framing" value={framing} onChange={(e) => setFraming(e.target.value)}>
      <option value="canvas">Full canvas — check position and scale</option>
      <option value="inspect">Inspect panels — readable, scrollable detail</option>
    </select>
    {framing === 'inspect' ? <p className="muted">Scroll to inspect song, requester, and alert text. This view uses a readable size and does not change your broadcast scale or position.</p> : null}
    <div className={`overlay-preview${framing === 'inspect' ? ' overlay-preview-inspect' : ''}`}><OverlayCanvas state={previewMode === 'sample' ? sampleData : live.state} config={previewSettings} preview inspect={framing === 'inspect'} /></div>
    <div className="actions"><button type="button" onClick={() => sample('request')}>Preview request</button><button type="button" onClick={() => sample('warning')}>Preview warning</button><button type="button" onClick={() => sample('general')}>Preview notice</button><button type="button" onClick={previewAdvertisement}>Preview advertisement</button></div>
    <div className="actions">
      <button type="button" disabled={busy || dirty || !canSend} onClick={() => sendTest('SongRequester')}>Send request to OBS</button>
      <button type="button" disabled={busy || dirty || !canSend} onClick={() => sendTest('WarningOverlay')}>Send warning to OBS</button>
      <button type="button" disabled={busy || dirty || !canSend} onClick={() => sendTest('GeneralOverlay')}>Send notice to OBS</button>
    </div>
    <p className="muted">Send buttons appear on your actual overlay. Preview buttons affect only this preview.</p>
    {message ? <p className="overlay-feedback" role="status">{message}</p> : null}
  </div>;
}
