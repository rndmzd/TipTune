import { useEffect, useRef, useState } from 'react';
import { Link, useLocation } from 'react-router-dom';

import { apiJson } from '../api';
import type { Device, QueueItem, QueueState } from '../types';
import { HeaderBar } from '../components/HeaderBar';
import { QueueCard } from '../components/QueueCard';

type QueueResp = { ok: true; queue: QueueState };
type DevicesResp = { ok: true; devices: Device[] };

export function DashboardPage() {
  const location = useLocation();

  const [status, setStatus] = useState<'loading' | 'ok' | 'error'>('loading');
  const [paused, setPaused] = useState<boolean>(false);
  const [nowPlaying, setNowPlaying] = useState<QueueItem | null>(null);
  const [queue, setQueue] = useState<(QueueItem | string)[]>([]);
  const [queueState, setQueueState] = useState<QueueState | null>(null);
  const [err, setErr] = useState<string>('');
  const [opBusy, setOpBusy] = useState(false);
  const [activeDeviceName, setActiveDeviceName] = useState<string>('');
  const [activeDeviceWarning, setActiveDeviceWarning] = useState<string>('');
  const [selectedDeviceWarning, setSelectedDeviceWarning] = useState<string>('');

  const selectedDeviceIdRef = useRef<string>('');
  const lastActiveDeviceIdRef = useRef<string>('');
  const hadActiveDeviceRef = useRef<boolean>(false);
  const hadSelectedDeviceAvailableRef = useRef<boolean>(true);

  useEffect(() => {
    selectedDeviceIdRef.current = String(queueState?.playback_device_id || '');
  }, [queueState?.playback_device_id]);

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

  async function refreshDevices() {
    try {
      const data = await apiJson<DevicesResp>('/api/spotify/devices');
      const devices = Array.isArray(data.devices) ? data.devices : [];
      const active = devices.find((d) => !!d && typeof d === 'object' && !!(d as any).is_active) as Device | undefined;

      const selectedId = selectedDeviceIdRef.current;
      const selectedDevice = selectedId
        ? (devices.find((d) => (d && typeof d === 'object' ? (d as any).id : '') === selectedId) as Device | undefined)
        : undefined;

      const activeId = (active && typeof active.id === 'string') ? active.id : '';
      const activeName = (active && typeof active.name === 'string') ? active.name : '';

      const selectedName = (selectedDevice && typeof selectedDevice.name === 'string') ? selectedDevice.name : '';
      const selectedLabel = selectedName || (selectedDevice && typeof selectedDevice.id === 'string' ? selectedDevice.id : '');
      const activeLabel = activeName || activeId;
      const displayLabel = activeLabel || selectedLabel;

      setActiveDeviceName(displayLabel);

      const hasActive = !!activeId;
      if (hasActive && lastActiveDeviceIdRef.current && activeId !== lastActiveDeviceIdRef.current) {
        setActiveDeviceWarning(`Spotify switched the active device to ${activeName || 'another device'}.`);
      } else if (!hasActive && hadActiveDeviceRef.current) {
        setActiveDeviceWarning('No active Spotify device detected. Open Spotify on a device and start playback.');
      } else if (hasActive) {
        setActiveDeviceWarning('');
      }

      lastActiveDeviceIdRef.current = activeId;
      hadActiveDeviceRef.current = hasActive;

      const selectedAvailable = !selectedId ? true : devices.some((d) => (d && typeof d === 'object' ? (d as any).id : '') === selectedId);
      if (!selectedAvailable && hadSelectedDeviceAvailableRef.current) {
        setSelectedDeviceWarning('Your selected playback device is no longer available. Please re-select a device in Settings.');
      } else if (selectedAvailable) {
        setSelectedDeviceWarning('');
      }
      hadSelectedDeviceAvailableRef.current = selectedAvailable;
    } catch {
      const fallbackLabel = String(queueState?.playback_device_name || queueState?.playback_device_id || '');
      if (fallbackLabel) setActiveDeviceName(fallbackLabel);
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
    refreshDevices().catch(() => {});
    const t = window.setInterval(() => {
      refresh(false).catch(() => {});
    }, 2000);
    const td = window.setInterval(() => {
      refreshDevices().catch(() => {});
    }, 10000);
    return () => {
      window.clearInterval(t);
      window.clearInterval(td);
    };
  }, [location.key]);

  const showDeviceWarning = status === 'ok' && queueState?.enabled === true && !queueState?.playback_device_id;
  const showPausedBanner = status === 'ok' && queueState?.enabled === true && paused;
  const showActiveDeviceWarning = status === 'ok' && queueState?.enabled === true && !!activeDeviceWarning;
  const showSelectedDeviceWarning = status === 'ok' && queueState?.enabled === true && !!selectedDeviceWarning;

  return (
    <>
      <HeaderBar
        title="TipTune"
      />

      <div className="row">
        <div className="card">
          <h2>Queue</h2>

          {status === 'ok' && queueState?.enabled === true ? (
            <div className="muted" style={{ marginTop: -6, marginBottom: 10, fontSize: 13 }}>
              Active Spotify device: {activeDeviceName ? activeDeviceName : '(none)'}
            </div>
          ) : null}

          {showActiveDeviceWarning ? (
            <div
              style={{
                marginTop: 10,
                border: '1px solid rgba(255, 193, 7, 0.35)',
                background: 'rgba(255, 193, 7, 0.10)',
                borderRadius: 10,
                padding: '10px 12px',
              }}
            >
              <div style={{ fontWeight: 650, marginBottom: 6 }}>Spotify device changed</div>
              <div className="muted">{activeDeviceWarning}</div>
            </div>
          ) : null}

          {showSelectedDeviceWarning ? (
            <div
              style={{
                marginTop: 10,
                border: '1px solid rgba(255, 193, 7, 0.35)',
                background: 'rgba(255, 193, 7, 0.10)',
                borderRadius: 10,
                padding: '10px 12px',
              }}
            >
              <div style={{ fontWeight: 650, marginBottom: 6 }}>Selected playback device unavailable</div>
              <div className="muted">
                {selectedDeviceWarning} Go to <Link to="/settings?dashboard=1">Settings</Link>.
              </div>
            </div>
          ) : null}

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
              <div className="muted">Song queue is paused, but the currently playing song will finish first.</div>
            </div>
          ) : null}

          <div className="actions">
            <span
              id="queueStatus"
              className={
                status === 'loading'
                  ? 'pill pillNeutral'
                  : status === 'error'
                    ? 'pill pillError'
                    : paused
                      ? 'pill pillWarn'
                      : 'pill pillSuccess'
              }
            >
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
