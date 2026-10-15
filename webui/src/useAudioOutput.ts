import { useEffect, useState } from 'react';
import { apiJson } from './api';
import { playerCommand } from './spotifyPlayer';

export function useAudioOutput(native: boolean, backendReady: boolean) {
  const [ready, setReady] = useState(!native);
  const [error, setError] = useState('');
  const [enabled, setEnabled] = useState<boolean | null>(null);
  useEffect(() => {
    if (!native || !backendReady) return;
    let cancelled = false;
    let timer: number;
    async function update() {
      try {
        const output = await apiJson<{ enabled: boolean }>('/api/audio/output', undefined, 5000);
        if (cancelled) return;
        // Recheck the device and new player contexts, including after a reload
        // or a Windows audio device disconnect.
        await playerCommand('audio_output_configure', { enabled: output.enabled });
        if (!cancelled) { setError(''); setEnabled(output.enabled); }
      } catch (exception: any) {
        if (cancelled) return;
        // Preserve local playback if OBS, the cable, or the backend disappears.
        await playerCommand('audio_output_configure', { enabled: false }).catch(() => {});
        if (!cancelled) { setError(`${exception?.message || String(exception)} Playing through normal speakers.`); setEnabled(false); }
      } finally {
        if (!cancelled) {
          setReady(true);
          timer = window.setTimeout(update, 3000);
        }
      }
    }
    update();
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [native, backendReady]);
  return { ready, error, enabled };
}
