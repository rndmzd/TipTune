import { useLayoutEffect, useRef, useState, type CSSProperties } from 'react';
import type { QueueItem } from '../types';
import { overlayDefaults, type OverlayState } from './types';
import './overlay.css';

function Artwork({ url }: { url?: string }) {
  const [failed, setFailed] = useState(false);
  useLayoutEffect(() => setFailed(false), [url]);
  return <div className="tt-artwork">
    {url && !failed ? <img src={url} alt="" onError={() => setFailed(true)} /> :
      <svg viewBox="0 0 48 48" fill="none" aria-hidden="true"><path d="M20 32V13l18-4v19M20 19l18-4" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" /><ellipse cx="14" cy="33" rx="6" ry="4" stroke="currentColor" strokeWidth="2.5" /><ellipse cx="32" cy="29" rx="6" ry="4" stroke="currentColor" strokeWidth="2.5" /></svg>}
  </div>;
}

function TrackText({ track }: { track: QueueItem }) {
  return <div className="tt-track-text">
    <div className="tt-title">{track.name || 'Unknown title'}</div>
    <div className="tt-artist">{track.artists?.join(', ') || 'Unknown artist'}</div>
    {track.requester ? <div className="tt-requester">Requested by <strong>{track.requester}</strong></div> : null}
  </div>;
}

export function OverlayCanvas({ state, config, preview = false, inspect = false }: {
  state: OverlayState | null; config?: Record<string, string>; preview?: boolean; inspect?: boolean;
}) {
  const viewport = useRef<HTMLDivElement>(null);
  const group = useRef<HTMLDivElement>(null);
  const [scale, setScale] = useState(1);
  const [margin, setMargin] = useState(32);
  const [inspectionHeight, setInspectionHeight] = useState(600);
  const inspection = preview && inspect;
  const settings = { ...overlayDefaults, ...state?.config, ...config };
  const show = (key: string) => settings[key] !== 'false';
  const enabled = preview || settings.mode === 'browser';
  const now = enabled && show('show_now_playing') ? state?.now_playing : null;
  const queue = enabled && show('show_queue') ? state?.upcoming.slice(0, Number(settings.queue_length)) || [] : [];
  const alerts = enabled && show('show_alerts') ? state?.alerts || [] : [];
  const contentKey = JSON.stringify([now, queue, alerts, settings]);

  useLayoutEffect(() => {
    const root = viewport.current, panel = group.current;
    if (!root || !panel) return;
    const resize = () => {
      if (inspection) {
        setScale(1); setMargin(20); setInspectionHeight(panel.offsetHeight + 40);
        return;
      }
      const w = root.clientWidth, h = root.clientHeight;
      const reference = Math.min(w / 1920, h / 1080);
      const edge = Math.min(Number(settings.margin) * reference, Math.min(w, h) / 4);
      const requested = reference * Number(settings.scale) / 100;
      setScale(Math.max(0.05, Math.min(requested, (w - edge * 2) / panel.offsetWidth, (h - edge * 2) / Math.max(panel.offsetHeight, 1))));
      setMargin(edge);
    };
    const observer = new ResizeObserver(resize);
    observer.observe(root); observer.observe(panel); resize();
    return () => observer.disconnect();
  }, [contentKey, inspection]);

  const hex = settings.background_color.replace('#', '');
  const background = `rgba(${parseInt(hex.slice(0, 2), 16)},${parseInt(hex.slice(2, 4), 16)},${parseInt(hex.slice(4, 6), 16)},${Number(settings.opacity) / 100})`;
  const style = {
    '--tt-accent': settings.accent_color, '--tt-text': settings.text_color,
    '--tt-background': background, '--tt-scale': scale, '--tt-margin': `${margin}px`,
    '--tt-inspection-height': `${inspectionHeight}px`,
  } as CSSProperties;
  return <div ref={viewport} className={`tt-overlay tt-${settings.layout} tt-${settings.position} tt-motion-${settings.motion}${inspection ? ' tt-inspect' : ''}`} style={style} data-testid="overlay-canvas">
    <div ref={group} className="tt-group">
      {alerts.length ? <div className="tt-alerts" aria-live="polite">{alerts.map((alert) => <div key={alert.id} className={`tt-alert tt-alert-${alert.kind}`}>
        <div className="tt-alert-heading"><span className="tt-status-dot" /><strong>{({ request: 'Song requested', warning: 'Request notice', general: 'Queue update', now_playing: 'Now Playing' } as Record<string, string>)[alert.kind] || 'TipTune'}</strong>{alert.requester ? <span className="tt-alert-requester">{alert.requester}</span> : null}</div>
        <div className="tt-alert-message">{alert.message}</div>
      </div>)}</div> : null}
      {now ? <section className="tt-now" aria-label="Now Playing">
        <div className="tt-panel-heading"><span className="tt-status-dot" /><strong>{state?.status.starting ? 'Starting' : state?.status.playback_paused ? 'Playback paused' : 'Now Playing'}</strong><span className="tt-source">{now.source === 'youtube' ? 'YouTube' : 'Spotify'}</span></div>
        <div className="tt-now-track">{show('show_artwork') ? <Artwork url={now.album_image_url} /> : null}<TrackText track={now} /></div>
      </section> : null}
      {queue.length ? <section className="tt-queue" aria-label="Upcoming queue">
        <div className="tt-panel-heading"><strong>{state?.status.queue_paused ? 'Queue paused' : state?.status.starting && !now ? 'Starting next song' : 'Up next'}</strong><span className="tt-queue-count">{state?.total_queue_count} queued</span></div>
        <ol>{queue.map((track, i) => <li key={track.queue_entry_id || i}><span className="tt-order">{String(i + 1).padStart(2, '0')}</span><TrackText track={track} /><span className="tt-queue-source">{track.source === 'youtube' ? 'YT' : 'SP'}</span></li>)}</ol>
        {(state?.total_queue_count || 0) > queue.length ? <div className="tt-more">+{state!.total_queue_count - queue.length} more in the queue</div> : null}
      </section> : null}
    </div>
  </div>;
}
