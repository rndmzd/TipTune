import { useEffect, useRef, useState } from 'react';
import { invoke } from '@tauri-apps/api/core';
import { HeaderBar } from '../components/HeaderBar';

// Serialize route cleanup, layout and reload calls, including React StrictMode remounts.
let pending: Promise<unknown> = Promise.resolve();
function playerCommand(command: string, args?: Record<string, unknown>) {
  const next = pending.catch(() => {}).then(() => invoke(command, args));
  pending = next;
  return next;
}

export function SpotifyPage() {
  const host = useRef<HTMLDivElement>(null);
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  const desktop = !!(window as any).__TAURI_INTERNALS__;

  useEffect(() => {
    if (!desktop || !host.current) return;
    let cancelled = false;
    let frame = 0;
    const place = () => {
      window.cancelAnimationFrame(frame);
      frame = window.requestAnimationFrame(() => {
        if (cancelled || !host.current) return;
        const rect = host.current.getBoundingClientRect();
        playerCommand('spotify_player_show', { x: rect.x, y: rect.y, width: rect.width, height: rect.height })
          .then(() => { if (!cancelled) setError(''); })
          .catch((e) => { if (!cancelled) setError(String(e)); });
      });
    };
    const observer = new ResizeObserver(place);
    observer.observe(host.current);
    window.addEventListener('resize', place);
    window.addEventListener('scroll', place, true);
    place();
    return () => {
      cancelled = true;
      window.cancelAnimationFrame(frame);
      observer.disconnect();
      window.removeEventListener('resize', place);
      window.removeEventListener('scroll', place, true);
      playerCommand('spotify_player_hide').catch(() => {});
    };
  }, [desktop, retry]);

  return (
    <div className="spotifyPage">
      <HeaderBar title="Spotify" showMiniPlayer={false} />
      <div className="spotifyToolbar">
        <p className="muted">Sign in with the same Spotify account connected to TipTune. Your login is saved on this computer.
          {' '}Select this Web Player in Settings to play song requests. Spotify may occasionally ask you to sign in again.</p>
        {desktop ? <button type="button" onClick={() => {
          playerCommand('spotify_player_reload').then(() => setRetry((n) => n + 1)).catch((e) => setError(String(e)));
        }}>Reload player</button> : null}
      </div>
      {error ? <div className="card" role="alert">Unable to open Spotify: {error}
        <button type="button" onClick={() => setRetry((n) => n + 1)}>Retry</button>
      </div> : null}
      <div ref={host} className="spotifyPlayerHost" aria-label="Spotify web player">
        {!desktop ? <div className="card">The embedded player is available in the TipTune desktop app.
          {' '}<a href="https://open.spotify.com/" target="_blank" rel="noreferrer">Open Spotify Web Player</a>
        </div> : <p className="muted">Loading Spotify…</p>}
      </div>
    </div>
  );
}
