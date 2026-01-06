import { useEffect, useRef, useState } from 'react';
import { Link, useLocation } from 'react-router-dom';

import { apiJson } from '../api';
import type { Device, QueueItem, QueueState } from '../types';
import { HeaderBar } from '../components/HeaderBar';
import { QueueCard } from '../components/QueueCard';

type QueueResp = { ok: true; queue: QueueState };
type DevicesResp = { ok: true; devices: Device[] };
type SearchTracksResp = { ok: true; tracks: QueueItem[] };

export function DashboardPage() {
  const location = useLocation();

  const [status, setStatus] = useState<'loading' | 'ok' | 'error'>('loading');
  const [paused, setPaused] = useState<boolean>(false);
  const [nowPlaying, setNowPlaying] = useState<QueueItem | null>(null);
  const [queue, setQueue] = useState<(QueueItem | string)[]>([]);
  const [queueState, setQueueState] = useState<QueueState | null>(null);
  const [err, setErr] = useState<string>('');
  const [opBusy, setOpBusy] = useState(false);
  const [dragFromIndex, setDragFromIndex] = useState<number | null>(null);
  const [dragOverIndex, setDragOverIndex] = useState<number | null>(null);
  const dragFromIndexRef = useRef<number | null>(null);
  const dragOverIndexRef = useRef<number | null>(null);
  const dragPointerActiveRef = useRef<boolean>(false);
  const queueOutRef = useRef<HTMLDivElement | null>(null);
  const dragLastClientYRef = useRef<number | null>(null);
  const dragAutoScrollRafRef = useRef<number | null>(null);
  const [activeDeviceName, setActiveDeviceName] = useState<string>('');
  const [activeDeviceWarning, setActiveDeviceWarning] = useState<string>('');
  const [selectedDeviceWarning, setSelectedDeviceWarning] = useState<string>('');

  const [obsNowPlayingBusy, setObsNowPlayingBusy] = useState<boolean>(false);
  const [obsNowPlayingMsg, setObsNowPlayingMsg] = useState<string>('');

  const [addTrackOpen, setAddTrackOpen] = useState<boolean>(false);
  const [searchQ, setSearchQ] = useState<string>('');
  const [searchBusy, setSearchBusy] = useState<boolean>(false);
  const [searchErr, setSearchErr] = useState<string>('');
  const [searchResults, setSearchResults] = useState<QueueItem[]>([]);
  const searchSeqRef = useRef<number>(0);

  const selectedDeviceIdRef = useRef<string>('');
  const lastActiveDeviceIdRef = useRef<string>('');
  const hadActiveDeviceRef = useRef<boolean>(false);
  const hadSelectedDeviceAvailableRef = useRef<boolean>(true);

  const [playbackPosMs, setPlaybackPosMs] = useState<number | null>(null);
  const playbackTickRef = useRef<number | null>(null);
  const playbackTickLastRef = useRef<number>(0);
  const playbackIsPlayingRef = useRef<boolean>(false);

  function fmtTime(ms: number): string {
    const n = Number(ms);
    if (!Number.isFinite(n) || n < 0) return '0:00';
    const s = Math.floor(n / 1000);
    const m = Math.floor(s / 60);
    const r = s % 60;
    return `${m}:${String(r).padStart(2, '0')}`;
  }

  async function sendNowPlayingToObs() {
    if (obsNowPlayingBusy || opBusy) return;
    setObsNowPlayingBusy(true);
    setObsNowPlayingMsg('');
    try {
      await apiJson<{ ok: true }>('/api/obs/now_playing', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: '{}',
      });
      setObsNowPlayingMsg('Sent to OBS.');
    } catch (e: any) {
      setObsNowPlayingMsg(e?.message ? String(e.message) : String(e));
    } finally {
      setObsNowPlayingBusy(false);
    }
  }

  useEffect(() => {
    dragOverIndexRef.current = dragOverIndex;
  }, [dragOverIndex]);

  useEffect(() => {
    function onPointerMove(ev: PointerEvent) {
      if (!dragPointerActiveRef.current) return;
      if (opBusy) return;

      dragLastClientYRef.current = ev.clientY;

      const el = document.elementFromPoint(ev.clientX, ev.clientY) as any;
      const target = el?.closest ? el.closest('[data-queue-index]') : null;
      const idxRaw = target ? (target as HTMLElement).getAttribute('data-queue-index') : null;
      const idx = idxRaw != null ? Number.parseInt(String(idxRaw), 10) : null;
      const next = Number.isInteger(idx) ? (idx as number) : null;

      setDragOverIndex((prev) => (prev === next ? prev : next));

      if (dragAutoScrollRafRef.current == null) {
        const step = () => {
          if (!dragPointerActiveRef.current) {
            dragAutoScrollRafRef.current = null;
            return;
          }
          const scroller = queueOutRef.current;
          const y = dragLastClientYRef.current;
          if (scroller && typeof y === 'number') {
            const rect = scroller.getBoundingClientRect();
            const threshold = 48;
            const maxSpeed = 14;

            const distTop = y - rect.top;
            const distBottom = rect.bottom - y;

            let speed = 0;
            if (distTop >= 0 && distTop < threshold) {
              const t = Math.max(0, Math.min(1, (threshold - distTop) / threshold));
              speed = -Math.round(1 + t * maxSpeed);
            } else if (distBottom >= 0 && distBottom < threshold) {
              const t = Math.max(0, Math.min(1, (threshold - distBottom) / threshold));
              speed = Math.round(1 + t * maxSpeed);
            }

            if (speed !== 0) {
              scroller.scrollTop += speed;
            }
          }

          dragAutoScrollRafRef.current = window.requestAnimationFrame(step);
        };

        dragAutoScrollRafRef.current = window.requestAnimationFrame(step);
      }
    }

    function onPointerUp() {
      if (!dragPointerActiveRef.current) return;
      dragPointerActiveRef.current = false;

      const from = dragFromIndexRef.current;
      const to = dragOverIndexRef.current;

      dragFromIndexRef.current = null;
      dragOverIndexRef.current = null;
      dragLastClientYRef.current = null;
      setDragFromIndex(null);
      setDragOverIndex(null);

      if (dragAutoScrollRafRef.current != null) {
        window.cancelAnimationFrame(dragAutoScrollRafRef.current);
        dragAutoScrollRafRef.current = null;
      }

      if (opBusy) return;
      if (!Number.isInteger(from)) return;
      if (!Number.isInteger(to)) return;
      move(from as number, to as number).catch(() => {});
    }

    window.addEventListener('pointermove', onPointerMove);
    window.addEventListener('pointerup', onPointerUp);
    window.addEventListener('pointercancel', onPointerUp);
    return () => {
      window.removeEventListener('pointermove', onPointerMove);
      window.removeEventListener('pointerup', onPointerUp);
      window.removeEventListener('pointercancel', onPointerUp);
    };
  }, [opBusy, queue.length]);

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

  async function searchTracks(q: string) {
    const qs = String(q ?? '').trim();
    if (qs.length < 2) {
      setSearchErr('Enter at least 2 characters.');
      setSearchResults([]);
      return;
    }

    const seq = ++searchSeqRef.current;
    setSearchBusy(true);
    try {
      const data = await apiJson<SearchTracksResp>(`/api/spotify/search?q=${encodeURIComponent(qs)}&limit=10`);
      if (seq !== searchSeqRef.current) return;

      const tracks = Array.isArray(data.tracks) ? data.tracks : [];
      setSearchResults(tracks);
      setSearchErr('');
    } catch (e: any) {
      if (seq !== searchSeqRef.current) return;
      setSearchResults([]);
      setSearchErr(e?.message ? String(e.message) : String(e));
    } finally {
      if (seq === searchSeqRef.current) setSearchBusy(false);
    }
  }

  async function addTrack(item: QueueItem) {
    if (opBusy) return;
    const uri = typeof item?.uri === 'string' && item.uri.trim() !== '' ? item.uri.trim() : '';
    if (!uri) return;

    setOpBusy(true);
    try {
      await post('/api/queue/add', { uri });
    } finally {
      await refresh(true);
      setOpBusy(false);
    }
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

  useEffect(() => {
    const serverPos = typeof queueState?.playback_progress_ms === 'number' ? queueState!.playback_progress_ms! : null;
    if (serverPos === null) {
      setPlaybackPosMs(null);
      return;
    }

    setPlaybackPosMs(serverPos);
  }, [queueState?.playback_progress_ms, queueState?.playback_track_uri]);

  useEffect(() => {
    playbackIsPlayingRef.current = !!queueState?.playback_is_playing;

    if (playbackTickRef.current != null) {
      window.clearInterval(playbackTickRef.current);
      playbackTickRef.current = null;
    }

    if (!queueState?.playback_is_playing) return;
    if (typeof queueState?.playback_progress_ms !== 'number') return;

    playbackTickLastRef.current = Date.now();
    playbackTickRef.current = window.setInterval(() => {
      if (!playbackIsPlayingRef.current) return;
      const now = Date.now();
      const delta = now - playbackTickLastRef.current;
      if (delta <= 0) return;
      playbackTickLastRef.current = now;
      setPlaybackPosMs((prev) => {
        if (typeof prev !== 'number') return prev;
        return prev + delta;
      });
    }, 250);

    return () => {
      if (playbackTickRef.current != null) {
        window.clearInterval(playbackTickRef.current);
        playbackTickRef.current = null;
      }
    };
  }, [queueState?.playback_is_playing, queueState?.playback_track_uri, queueState?.playback_progress_ms]);

  const showDeviceWarning = status === 'ok' && queueState?.enabled === true && !queueState?.playback_device_id;
  const showPausedBanner = status === 'ok' && queueState?.enabled === true && paused;
  const showActiveDeviceWarning = status === 'ok' && queueState?.enabled === true && !!activeDeviceWarning;
  const showSelectedDeviceWarning = status === 'ok' && queueState?.enabled === true && !!selectedDeviceWarning;
  const canUseQueueControls = status === 'ok' && queueState?.enabled === true;

  const durationMs = typeof nowPlaying?.duration_ms === 'number' ? nowPlaying.duration_ms : null;
  const safePosMs = typeof playbackPosMs === 'number' ? playbackPosMs : null;
  const posClampedMs = safePosMs != null && durationMs != null ? Math.max(0, Math.min(safePosMs, durationMs)) : safePosMs;
  const pct = durationMs && posClampedMs != null && durationMs > 0 ? Math.max(0, Math.min(1, posClampedMs / durationMs)) : null;

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
              disabled={opBusy || paused}
            >
              Pause
            </button>
            <button
              type="button"
              onClick={async () => {
                await post('/api/queue/resume');
                await refresh(true);
              }}
              disabled={opBusy || !paused}
            >
              Resume
            </button>
            <button type="button" onClick={() => refresh(true)} disabled={opBusy}>
              Refresh
            </button>
            <button
              type="button"
              onClick={() => {
                setAddTrackOpen((v) => {
                  const next = !v;
                  if (!next) {
                    searchSeqRef.current += 1;
                    setSearchBusy(false);
                    setSearchErr('');
                    setSearchResults([]);
                    setSearchQ('');
                  }
                  return next;
                });
              }}
              disabled={opBusy || !canUseQueueControls}
            >
              {addTrackOpen ? 'Close Add Track' : 'Add Track'}
            </button>
          </div>

          {addTrackOpen ? (
            <div
              style={{
                marginTop: 12,
                border: '1px solid #2a3a66',
                background: '#0e1530',
                borderRadius: 10,
                padding: '10px 12px',
              }}
            >
              <div style={{ fontWeight: 650, marginBottom: 6 }}>Add Track</div>
              <div className="muted" style={{ marginBottom: 10 }}>
                Search Spotify for a track, then add it to the end of the queue.
              </div>

              <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center' }}>
                <input
                  type="text"
                  value={searchQ}
                  placeholder="Artist and song title"
                  onChange={(e) => setSearchQ(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') {
                      e.preventDefault();
                      searchTracks(searchQ).catch(() => {});
                    }
                  }}
                  style={{ flex: 1, minWidth: 220, width: 'auto' }}
                />
                <button type="button" onClick={() => searchTracks(searchQ)} disabled={searchBusy || opBusy}>
                  {searchBusy ? 'Searching…' : 'Search'}
                </button>
                <button
                  type="button"
                  onClick={() => {
                    searchSeqRef.current += 1;
                    setSearchBusy(false);
                    setSearchErr('');
                    setSearchResults([]);
                    setSearchQ('');
                  }}
                  disabled={searchBusy || opBusy}
                >
                  Clear
                </button>
              </div>

              {searchErr ? <div className="muted" style={{ marginTop: 8 }}>Error: {searchErr}</div> : null}

              <div className="queueOut" style={{ maxHeight: 320, marginTop: 10 }}>
                {searchResults.length ? (
                  searchResults.map((item, i) => (
                    <QueueCard
                      key={String(item.track_id || item.uri || i)}
                      item={item}
                      indexLabel={`Result #${i + 1}`}
                      allowDelete={false}
                      rightActions={
                        <button
                          className="queueIconBtn queueIconBtnLarge"
                          type="button"
                          title="Add to queue"
                          onClick={(e) => {
                            e.preventDefault();
                            e.stopPropagation();
                            addTrack(item).catch(() => {});
                          }}
                          disabled={opBusy || !(typeof item?.uri === 'string' && item.uri.trim() !== '')}
                        >
                          +
                        </button>
                      }
                    />
                  ))
                ) : (
                  <div className="muted">Search results will appear here.</div>
                )}
              </div>
            </div>
          ) : null}

          {status === 'error' ? <div className="muted">Error: {err}</div> : null}

          <label>Now playing</label>
          <div className="queueOut">
            {nowPlaying ? (
              <div>
                <QueueCard item={nowPlaying} indexLabel="Now" allowDelete={false} extraClass="queueCardNowPlaying" />
                <div style={{ marginTop: 8, display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                  <button type="button" onClick={() => sendNowPlayingToObs().catch(() => {})} disabled={opBusy || obsNowPlayingBusy}>
                    {obsNowPlayingBusy ? 'Sending…' : 'Send info to OBS'}
                  </button>
                  {obsNowPlayingMsg ? <div className="muted">{obsNowPlayingMsg}</div> : null}
                </div>
                {durationMs && posClampedMs != null ? (
                  <div style={{ marginTop: 8 }}>
                    <div style={{ height: 8, borderRadius: 999, background: '#0b1020', border: '1px solid #2a3a66', overflow: 'hidden' }}>
                      <div
                        style={{
                          height: '100%',
                          width: `${Math.round((pct ?? 0) * 1000) / 10}%`,
                          background: 'rgba(138, 180, 255, 0.65)',
                        }}
                      />
                    </div>
                    <div className="muted" style={{ marginTop: 6, display: 'flex', justifyContent: 'space-between' }}>
                      <span>{fmtTime(posClampedMs)}</span>
                      <span>{fmtTime(durationMs)}</span>
                    </div>
                  </div>
                ) : null}
              </div>
            ) : (
              <div className="queueOut">(none)</div>
            )}
          </div>

          <label>Up next</label>
          <div className="queueOut" ref={queueOutRef}>
            {queue.length ? (
              queue.map((item, i) => (
                <div
                  key={i}
                  className="queueDragItem"
                  data-queue-index={i}
                >
                  <QueueCard
                    item={item as any}
                    indexLabel={`#${i + 1}`}
                    allowDelete={true}
                    onDelete={() => remove(i)}
                    showDragHandle={true}
                    onDragHandlePointerDown={(e) => {
                      if (opBusy) return;
                      e.preventDefault();
                      e.stopPropagation();
                      dragPointerActiveRef.current = true;
                      dragFromIndexRef.current = i;
                      dragOverIndexRef.current = i;
                      setDragFromIndex(i);
                      setDragOverIndex(i);
                      try {
                        (e.currentTarget as any).setPointerCapture?.(e.pointerId);
                      } catch {}
                    }}
                    extraClass={[dragFromIndex === i ? 'queueCardDragging' : '', dragOverIndex === i ? 'queueCardDragOver' : ''].filter(Boolean).join(' ')}
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
                </div>
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
