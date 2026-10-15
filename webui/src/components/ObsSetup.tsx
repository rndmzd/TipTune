import { useState, type ReactNode } from 'react';
import { apiJson } from '../api';
import type { ObsStatus } from '../types';
import { overlayDefaults } from '../overlay/types';
import { ConfiguredSection } from './ConfiguredSection';
import './overlay-settings.css';

export function ObsSetup({ cfg, status, statusError, dirty, pending, sceneControls, onChange, onRefreshStatus }: {
  cfg: Record<string, Record<string, string>>; status: ObsStatus | null; statusError: string;
  dirty: boolean; pending: boolean; sceneControls: ReactNode;
  onChange: (section: string, key: string, value: string) => void; onRefreshStatus: () => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const enabled = cfg.OBS?.enabled?.toLowerCase() === 'true';
  const connected = enabled && !!status?.connected;
  const scene = cfg.OBS?.scene_name || '';
  const sourceName = cfg.Overlay?.source_name || overlayDefaults.source_name;
  const configured = connected && !!scene && !!status?.browser_source?.configured
    && status.status?.main_scene === scene && status.browser_source.name === sourceName;
  const waiting = enabled && !!status && !connected && !statusError;
  const label = !enabled ? 'Manual setup' : statusError ? 'Status unavailable' : !status ? 'Checking OBS…'
    : waiting ? 'Waiting for OBS' : configured ? 'Configured' : 'Needs setup';
  const url = status?.overlay_url || 'http://127.0.0.1:8765/overlay';
  const dimensions = status?.viewport || { width: 1920, height: 1080 };

  async function addBrowser() {
    setBusy(true); setMessage('Adding the browser source…');
    try {
      const response = await apiJson<{ result: { legacy_errors?: string[] } }>('/api/obs/ensure_browser_source', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ scene_name: scene, source_name: sourceName, activate: cfg.Overlay?.mode === 'browser' }),
      }, 45000);
      setMessage(response.result.legacy_errors?.length
        ? `Browser source added. Hide these old text sources in OBS: ${response.result.legacy_errors.join('; ')}`
        : 'Browser source added to OBS.');
      await onRefreshStatus();
    } catch (error: any) { setMessage(error?.message || 'Could not add the browser source. Refresh status and try again.'); }
    finally { setBusy(false); }
  }

  return <ConfiguredSection id="settings-obs-setup" title="Add to OBS" ready={configured || (waiting && !!scene) || !enabled}
    status={label} tone={configured ? 'success' : connected ? 'warn' : 'neutral'} pending={pending}
    className="overlay-settings-card" actions={<button type="button" disabled={busy} onClick={() => onRefreshStatus()}>Refresh status</button>}
    feedback={statusError ? <p className="settingsDisclosureFeedback muted" role="status">{statusError}</p> : undefined}>
    <div className="overlay-settings">
      {sceneControls}
      {waiting ? <p className="muted">Open OBS to check the scene and browser source.</p> : null}
      {connected ? <p className="muted">Browser source: {configured ? 'configured' : 'needs setup'}</p> : null}
      {connected && status?.browser_source?.error ? <p role="status">{status.browser_source.error}</p> : null}
      <label htmlFor="overlay-source-name">OBS source name</label>
      <input id="overlay-source-name" maxLength={100} value={sourceName} onChange={(e) => onChange('Overlay', 'source_name', e.target.value)} />
      {enabled ? <>
        <div className="actions"><button type="button" disabled={busy || dirty || !connected || !scene} onClick={addBrowser}>
          {busy ? 'Working…' : 'Add browser overlay'}
        </button></div>
        {dirty ? <p className="muted">Save your changes before adding the source to OBS.</p> : null}
      </> : <p className="muted">Enable the OBS connection for automatic source setup. Manual browser display works without it.</p>}
      <label htmlFor="overlay-url">Browser source URL</label>
      <div className="overlay-copy"><input id="overlay-url" value={url} readOnly /><button type="button" onClick={async () => {
        try { await navigator.clipboard.writeText(url); setMessage('URL copied.'); }
        catch { setMessage('Select the URL and copy it manually.'); }
      }}>Copy URL</button></div>
      <p className="muted">In OBS, add a Browser source with this URL. Set width {dimensions.width}, height {dimensions.height}, and 30 FPS. Keep both shutdown and scene-activation refresh options off. Start TipTune first.</p>
      {message ? <p className="overlay-feedback" role="status">{message}</p> : null}
    </div>
  </ConfiguredSection>;
}
