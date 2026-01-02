import { useEffect, useState } from 'react';
import { Link, useLocation } from 'react-router-dom';

import { apiJson } from '../api';
import type { QueueItem, QueueState } from '../types';
import { HeaderBar } from '../components/HeaderBar';
import { QueueCard } from '../components/QueueCard';

type QueueResp = { ok: true; queue: QueueState };

export function DashboardPage() {
  const location = useLocation();

  const [status, setStatus] = useState<'loading' | 'ok' | 'error'>('loading');
  const [paused, setPaused] = useState<boolean>(false);
  const [nowPlaying, setNowPlaying] = useState<QueueItem | null>(null);
  const [queue, setQueue] = useState<(QueueItem | string)[]>([]);
  const [queueState, setQueueState] = useState<QueueState | null>(null);
  const [err, setErr] = useState<string>('');
  const [opBusy, setOpBusy] = useState(false);

  async function refresh(force?: boolean) {
    if (!force && opBusy) return;
    try {
      const data = await apiJson<QueueResp>('/api/queue');
      const st = data.queue ?? {};
      setQueueState(st);
      setPaused(!!st.paused);

      const np =
        st.now_playing_item && typeof st.now_playing_item === 'object'
          ? (st.now_playing_item as QueueItem)
          : st.now_playing_track
            ? (st.now_playing_track as any as QueueItem)
            : null;
      setNowPlaying(np);

      const items = Array.isArray(st.queued_items) && st.queued_items.length ? st.queued_items : Array.isArray(st.queued_tracks) ? st.queued_tracks : [];
      setQueue(items as any);

      setStatus('ok');
      setErr('');
    } catch (e: any) {
      setStatus('error');
      setErr(e?.message ? String(e.message) : String(e));
    }
  }

  async function post(path: string, body?: unknown) {
    await apiJson(path, {
      method: 'POST',
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
  }

  async function move(fromIndex: number, toIndex: number) {
    if (opBusy) return;
    if (!Number.isInteger(fromIndex) || !Number.isInteger(toIndex)) return;
    if (fromIndex === toIndex) return;
    if (fromIndex < 0 || toIndex < 0 || fromIndex >= queue.length || toIndex >= queue.length) return;

    setOpBusy(true);
    try {
      await post('/api/queue/move', { from_index: fromIndex, to_index: toIndex });
    } finally {
      await refresh(true);
      setOpBusy(false);
    }
  }

  async function remove(index: number) {
    if (opBusy) return;
    if (!Number.isInteger(index) || index < 0 || index >= queue.length) return;

    const label = (() => {
      const item = queue[index];
      if (item && typeof item === 'object') {
        const o = item as QueueItem;
        return o.name || o.uri || '(unknown)';
      }
      return String(item ?? '(unknown)');
    })();

    if (!confirm(`Remove from queue?\n\n${label}`)) return;

    setOpBusy(true);
    try {
      await post('/api/queue/delete', { index });
    } finally {
      await refresh(true);
      setOpBusy(false);
    }
  }

  useEffect(() => {
    refresh(true).catch(() => {});
    const t = window.setInterval(() => {
      refresh(false).catch(() => {});
    }, 2000);
    return () => window.clearInterval(t);
  }, [location.key]);

  const showDeviceWarning = status === 'ok' && queueState?.enabled === true && !queueState?.playback_device_id;
  const showPausedBanner = status === 'ok' && queueState?.enabled === true && paused;

  return (
    <>
      <HeaderBar
        title="TipTune"
      />

      <div className="row">
        <div className="card">
          <h2>Queue</h2>

          {showDeviceWarning ? (
            <div
              style={{
                marginTop: 10,
                border: '1px solid rgba(255, 193, 7, 0.35)',
                background: 'rgba(255, 193, 7, 0.10)',
                borderRadius: 10,
                padding: '10px 12px',
              }}
            >
              <div style={{ fontWeight: 650, marginBottom: 6 }}>No Spotify playback device selected</div>
              <div className="muted">
                Make sure Spotify is open (so it can register a device), then go to{' '}
                <Link to="/settings?dashboard=1">Settings</Link> and select a playback device.
              </div>
            </div>
          ) : null}

          {showPausedBanner ? (
            <div
              style={{
                marginTop: 10,
                border: '1px solid rgba(220, 53, 69, 0.45)',
                background: 'rgba(220, 53, 69, 0.12)',
                borderRadius: 10,
                padding: '10px 12px',
              }}
            >
              <div style={{ fontWeight: 650, marginBottom: 6 }}>Queue is paused</div>
              <div className="muted">The current song can finish, but new songs will wait until you click Resume.</div>
            </div>
          ) : null}

          <div className="actions">
            <span id="queueStatus" className="pill">
              {status === 'loading' ? 'Loading…' : status === 'error' ? 'Error' : paused ? 'Paused' : 'Running'}
            </span>
            <button
              type="button"
              onClick={async () => {
                await post('/api/queue/pause');
                await refresh(true);
              }}
              disabled={opBusy}
            >
              Pause
            </button>
            <button
              type="button"
              onClick={async () => {
                await post('/api/queue/resume');
                await refresh(true);
              }}
              disabled={opBusy}
            >
              Resume
            </button>
            <button type="button" onClick={() => refresh(true)} disabled={opBusy}>
              Refresh
            </button>
          </div>

          {status === 'error' ? <div className="muted">Error: {err}</div> : null}

          <label>Now playing</label>
          <div className="queueOut">
            {nowPlaying ? (
              <QueueCard item={nowPlaying} indexLabel="Now" allowDelete={false} extraClass="queueCardNowPlaying" />
            ) : (
              <div className="queueOut">(none)</div>
            )}
          </div>

          <label>Up next</label>
          <div className="queueOut">
            {queue.length ? (
              queue.map((item, i) => (
                <QueueCard
                  key={i}
                  item={item as any}
                  indexLabel={`#${i + 1}`}
                  allowDelete={true}
                  onDelete={() => remove(i)}
                  rightActions={
                    <>
                      <button type="button" onClick={() => move(i, i - 1)} disabled={opBusy || i === 0}>
                        Up
                      </button>
                      <button type="button" onClick={() => move(i, i + 1)} disabled={opBusy || i === queue.length - 1}>
                        Down
                      </button>
                    </>
                  }
                />
              ))
            ) : (
              <div>(empty)</div>
            )}
          </div>
        </div>
      </div>
    </>
  );
}
