import { useState } from 'react';
import { apiJson } from '../api';
import type { ObsStatus } from '../types';
import { ConfiguredSection } from './ConfiguredSection';
import './overlay-settings.css';

export function ObsAudioSettings({ status, error, audioOutputError = '', outputActive, enabled, pending, onEnabledChange, dirty = false, onRefresh, onboarding = false, onSave }: {
  status: ObsStatus | null; error: string; audioOutputError?: string; outputActive?: boolean | null;
  enabled: boolean; pending: boolean; onEnabledChange: (enabled: boolean) => void; dirty?: boolean; onRefresh: () => Promise<void>;
  onboarding?: boolean; onSave?: () => Promise<void>;
}) {
  const [busy, setBusy] = useState<'setup' | 'refresh' | 'save' | null>(null);
  const [feedback, setFeedback] = useState<{ text: string; error: boolean } | null>(null);
  const connected = !!status?.connected;
  const capture = status?.tiptune_audio_capture;
  const captureReady = !error && connected && !!capture?.configured;
  const configured = enabled && !pending && !audioOutputError && captureReady && (outputActive === undefined || outputActive === true);
  const unavailable = !capture || capture.state === 'unavailable';
  const manual = ['muted', 'disabled', 'needs_desktop', 'device_missing'].includes(capture?.state || '');
  const needsScene = capture?.target_matches && !capture.in_main_scene;
  const label = !enabled ? 'Disabled' : error ? 'Status unavailable' : !status ? 'Checking OBS…' : !connected ? 'Waiting for OBS'
    : audioOutputError ? 'Audio output unavailable' : configured ? 'Configured' : captureReady ? 'Connecting audio…'
    : capture?.state === 'needs_desktop' ? 'Open TipTune' : capture?.state === 'muted' ? 'Muted in OBS'
    : capture?.state === 'device_missing' ? 'VB-CABLE unavailable'
    : capture?.state === 'disabled' ? 'Disabled in OBS' : unavailable ? 'Status unavailable' : needsScene ? 'Not in scene' : 'Needs setup';
  const action = needsScene ? 'Add to scene' : capture?.input_exists ? 'Repair audio capture' : 'Set up audio capture';

  async function refresh() {
    try { await onRefresh(); }
    catch { setFeedback((previous) => previous?.error ? previous : { text: 'Could not refresh audio status. Try again.', error: true }); }
  }

  async function ensure() {
    setBusy('setup'); setFeedback(null);
    try {
      const response = await apiJson<{ result: { configured?: boolean; state?: string; errors?: string[] } }>('/api/obs/ensure_tiptune_audio_capture', { method: 'POST' }, 45000);
      const result = response.result;
      const text = result.errors?.length ? result.errors.join(' ')
        : result.configured ? 'OBS audio source configured.'
        : result.state === 'muted' ? 'Capture is set up. Unmute the source in OBS to include its sound.'
        : result.state === 'disabled' ? 'Capture is set up. Enable the source in OBS to include its sound.'
        : 'Capture could not be verified. Refresh audio status and try again.';
      setFeedback({ text, error: !!result.errors?.length || !result.configured && !['muted', 'disabled'].includes(result.state || '') });
    } catch (exception: any) {
      setFeedback({ text: exception?.message || 'Could not set up audio capture. Refresh audio status and try again.', error: true });
    } finally { await refresh(); setBusy(null); }
  }

  async function refreshManually() {
    setBusy('refresh'); setFeedback(null);
    await refresh(); setBusy(null);
  }

  async function save() {
    setBusy('save'); setFeedback(null);
    try { await onSave?.(); }
    catch (exception: any) { setFeedback({ text: exception?.message || 'Could not save OBS setup. Try again.', error: true }); }
    finally { setBusy(null); }
  }

  return <ConfiguredSection id="settings-obs-status" title="OBS audio sources" ready={!enabled && !onboarding || configured || !connected && !error} pending={pending}
    status={label} tone={configured ? 'success' : 'neutral'}
    actions={<>{onSave && pending ? <button type="button" disabled={!!busy} onClick={save}>{busy === 'save' ? 'Saving…' : 'Save OBS setup'}</button> : null}
      <button type="button" disabled={!!busy} onClick={refreshManually}>{busy === 'refresh' ? 'Checking audio…' : 'Refresh audio status'}</button></>}
    feedback={<>{enabled && (error || audioOutputError) ? <p className="settingsDisclosureFeedback muted" role="alert">{error || audioOutputError}</p> : null}
      {feedback ? <p className="settingsDisclosureFeedback" role={feedback.error ? 'alert' : 'status'}>{feedback.text}</p> : null}</>}>
    <div className="overlay-toggles"><label>
      <input type="checkbox" checked={enabled} aria-describedby="obs-audio-help" onChange={(event) => { setFeedback(null); onEnabledChange(event.target.checked); }} />
      Capture TipTune audio in OBS
    </label></div>
    <p id="obs-audio-help" className="muted">Uses VB-CABLE for YouTube and the Spotify player inside TipTune. OBS plays the sound through its monitoring device. When OBS is closed, TipTune uses your normal output.</p>
    <details className="obs-audio-guide" open={capture?.state === 'device_missing' ? true : undefined}>
      <summary>VB-CABLE installation and listening setup</summary>
      <ol>
        <li>Download <a href="https://vb-audio.com/Cable/" target="_blank" rel="noreferrer noopener" onClick={async (event) => {
          if (!(window as any).__TAURI_INTERNALS__) return;
          event.preventDefault();
          try { const { open } = await import('@tauri-apps/plugin-shell'); await open('https://vb-audio.com/Cable/'); }
          catch { setFeedback({ text: 'Open https://vb-audio.com/Cable/ in your browser to download VB-CABLE.', error: true }); }
        }}>VB-CABLE from VB-Audio</a>. Extract the download, run its Windows installer as administrator, and restart Windows. Dedicate this cable to TipTune.</li>
        <li>Keep Windows’ default output set to your usual speakers or headphones.</li>
        <li>In OBS → Settings → Audio, choose your speakers or headphones as the monitoring device. Keep CABLE Input dedicated to TipTune. TipTune’s setup button enables monitoring on its audio source. Exclude your monitoring device from Desktop Audio or Audio Output Capture sources to avoid capturing TipTune twice.</li>
        <li>Enable Capture TipTune audio in OBS, save, then select Set up audio capture. Play a request and check the TipTune Audio meter in OBS.</li>
      </ol>
      <p className="muted">If the meter moves but you hear nothing, check the OBS monitoring device, source mute, and volume. Reconnect your listening device and select it again if needed.</p>
    </details>
    {pending ? <p className="muted">Save changes to apply audio capture.</p>
      : !enabled ? null : error ? <p className="muted">Refresh audio status to check the capture.</p>
      : !connected ? <p className="muted">Open OBS to check the audio capture.</p>
      : <div className="obs-audio-content">
        {capture?.input_name ? <p className="muted">OBS source: <strong>{capture.input_name}</strong></p> : null}
        {capture?.state === 'needs_desktop' ? <p className="muted">{capture.message}</p> : null}
        {capture?.state === 'muted' ? <p className="muted">Unmute this source in OBS to include its sound.</p> : null}
        {capture?.state === 'disabled' ? <p className="muted">Enable this source in the selected OBS scene to include its sound.</p> : null}
        {capture?.state === 'device_missing' ? <p className="muted">Enable VB-CABLE in Windows sound settings, then refresh audio status.</p> : null}
        {capture?.state === 'needs_monitoring' ? <p className="muted">Set up audio capture to hear TipTune through OBS as well as on stream.</p> : null}
        {needsScene && status?.status?.main_scene ? <p className="muted">Add this source to the <strong>{status.status.main_scene}</strong> scene.</p> : null}
        {unavailable ? <p className="muted">Refresh audio status to check the capture.</p> : null}
        {!captureReady && !manual && !unavailable ? <button type="button" disabled={!!busy || dirty} onClick={ensure}>
          {busy === 'setup' ? 'Setting up audio…' : action}
        </button> : null}
        {dirty && !captureReady && !manual && !unavailable ? <p className="muted">Save changes before setting up audio capture.</p> : null}
      </div>}
  </ConfiguredSection>;
}
