import { useEffect, useMemo, useRef, useState } from 'react';

import { apiJson } from '../api';
import { HeaderBar } from '../components/HeaderBar';

function toLocalTimeLabel(v: any): string | null {
  try {
    const d = v instanceof Date ? v : new Date(v);
    if (Number.isNaN(d.getTime())) return null;
    return d.toLocaleString();
  } catch {
    return null;
  }
}

function summarize(item: any) {
  const ts = item && typeof item === 'object' ? (item as any).ts : null;
  const time = typeof ts === 'number' ? toLocalTimeLabel(ts * 1000) : toLocalTimeLabel(ts);

  const username = item && typeof item === 'object' ? (item as any).username : null;
  const status = item && typeof item === 'object' ? (item as any).status : null;
  const tipAmount = item && typeof item === 'object' ? (item as any).tip_amount : null;
  const tipMessage = item && typeof item === 'object' ? (item as any).tip_message : null;

  const song = item && typeof item === 'object' ? (item as any).song : null;
  const artist = item && typeof item === 'object' ? (item as any).artist : null;
  const songDetails = item && typeof item === 'object' ? (item as any).song_details : null;
  const resolvedUri = item && typeof item === 'object' ? (item as any).resolved_uri : null;
  const error = item && typeof item === 'object' ? (item as any).error : null;

  const spotifyTrack = item && typeof item === 'object' ? (item as any).spotify_track : null;
  const spotifyName = spotifyTrack && typeof spotifyTrack === 'object' ? (spotifyTrack as any).name : null;
  const spotifyArtistsRaw = spotifyTrack && typeof spotifyTrack === 'object' ? (spotifyTrack as any).artists : null;
  const spotifyAlbum = spotifyTrack && typeof spotifyTrack === 'object' ? (spotifyTrack as any).album : null;
  const spotifyAlbumImageUrl =
    spotifyTrack && typeof spotifyTrack === 'object' ? (spotifyTrack as any).album_image_url : null;

  const spotifyArtists = Array.isArray(spotifyArtistsRaw)
    ? spotifyArtistsRaw.filter((v: any) => typeof v === 'string' && v.trim() !== '').join(', ')
    : null;

  return {
    time,
    username,
    status,
    tipAmount,
    tipMessage,
    song,
    artist,
    songDetails,
    resolvedUri,
    error,
    spotifyName,
    spotifyArtists,
    spotifyAlbum,
    spotifyAlbumImageUrl,
  };
}

const artworkRequests = new Map<string, Promise<string | null>>();

function loadHistoryArtwork(uri: string): Promise<string | null> {
  const pending = artworkRequests.get(uri);
  if (pending) return pending;
  const request = apiJson<{ ok: true; track: { album_image_url?: string } | null }>(
    `/api/history/track?uri=${encodeURIComponent(uri)}`,
  ).then((response) => response.track?.album_image_url?.trim() || null)
    .catch(() => null)
    .finally(() => artworkRequests.delete(uri));
  artworkRequests.set(uri, request);
  return request;
}

function HistoryArtwork(props: { item: any; imageUrl: unknown; uri: unknown; songName: string }) {
  const container = useRef<HTMLDivElement>(null);
  const [loadedUrl, setLoadedUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const imageUrl = typeof props.imageUrl === 'string' ? props.imageUrl.trim() : '';
  const uri = typeof props.uri === 'string' ? props.uri.trim() : '';

  useEffect(() => {
    setLoadedUrl(null);
    setFailed(false);
    if (imageUrl || !uri || !(uri.startsWith('spotify:track:') || uri.includes('open.spotify.com/track/'))) return;
    let active = true;
    const observer = new IntersectionObserver((entries) => {
      if (!entries.some((entry) => entry.isIntersecting)) return;
      observer.disconnect();
      loadHistoryArtwork(uri).then((url) => {
        if (active) setLoadedUrl(url);
      });
    }, { rootMargin: '200px' });
    if (container.current) observer.observe(container.current);
    return () => {
      active = false;
      observer.disconnect();
    };
  }, [props.item, imageUrl, uri]);

  const src = imageUrl || loadedUrl;
  return (
    <div ref={container} style={{ width: 48, height: 48, flex: '0 0 auto', background: '#00000010', borderRadius: 8, overflow: 'hidden', display: 'grid', placeItems: 'center' }}>
      {src && !failed ? (
        <img src={src} alt={`Album artwork for ${props.songName}`} loading="lazy" onError={() => setFailed(true)}
          style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }} />
      ) : (
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" aria-label="Album artwork unavailable" role="img" style={{ opacity: 0.45 }}>
          <path d="M9 18V5l12-2v13M9 8l12-2" />
          <ellipse cx="6" cy="18" rx="3" ry="2" />
          <ellipse cx="18" cy="16" rx="3" ry="2" />
        </svg>
      )}
    </div>
  );
}

function HistoryCard(props: { item: any }) {
  const s = useMemo(() => summarize(props.item), [props.item]);

  const t = typeof s.spotifyName === 'string' && s.spotifyName.trim() !== '' ? s.spotifyName.trim() : null;
  const a = typeof s.spotifyArtists === 'string' && s.spotifyArtists.trim() !== '' ? s.spotifyArtists.trim() : null;
  const album = typeof s.spotifyAlbum === 'string' && s.spotifyAlbum.trim() !== '' ? s.spotifyAlbum.trim() : null;

  const fallbackArtist = typeof s.artist === 'string' && s.artist.trim() !== '' ? s.artist.trim() : null;
  const fallbackSong = typeof s.song === 'string' && s.song.trim() !== '' ? s.song.trim() : null;
  const fallbackSongDetails = typeof s.songDetails === 'string' && s.songDetails.trim() !== '' ? s.songDetails.trim() : null;

  const songName = t || fallbackSong || (fallbackSongDetails ? fallbackSongDetails.split(' - ').slice(1).join(' - ') : null) || 'request';
  const artistName = a || fallbackArtist || (fallbackSongDetails ? fallbackSongDetails.split(' - ')[0] : null);
  const subtitle = [artistName, album].filter(Boolean).join(' · ');

  const statusClass =
    s.status === 'added' ? 'pill pillInfo' : s.status === 'failed' ? 'pill pillWarn' : 'pill pillNeutral';

  return (
    <div className="card">
      <div className="cardHeader" style={{ display: 'flex', alignItems: 'center' }}>
        <HistoryArtwork item={props.item} imageUrl={s.spotifyAlbumImageUrl} uri={s.resolvedUri} songName={songName} />

        <div style={{ flex: 1, minWidth: 0, marginLeft: 12 }}>
          <div className="cardTitle" style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
            {songName}
          </div>
          {subtitle ? (
            <div className="muted" style={{ marginTop: 2, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
              {subtitle}
            </div>
          ) : null}

          <div className="cardMeta" style={{ marginTop: 6 }}>
            {typeof s.username === 'string' && s.username.trim() !== '' ? (
              <span className="pill pillNeutral">{s.username}</span>
            ) : null}
            {typeof s.tipAmount === 'number' ? (
              <span className="pill pillNeutral" style={{ marginLeft: 8 }}>{`${s.tipAmount} tokens`}</span>
            ) : null}
            {typeof s.status === 'string' && s.status.trim() !== '' ? (
              <span className={statusClass} style={{ marginLeft: 8 }}>
                {s.status}
              </span>
            ) : null}
            {s.time ? <span style={{ marginLeft: 10 }}>{s.time}</span> : null}
          </div>
        </div>
      </div>
      <div className="cardBody">
        {typeof s.error === 'string' && s.error.trim() !== '' ? <div className="message">{s.error}</div> : null}
        {typeof s.tipMessage === 'string' && s.tipMessage.trim() !== '' ? (
          <details>
            <summary>Tip message</summary>
            <pre>{s.tipMessage}</pre>
          </details>
        ) : null}
        {typeof s.resolvedUri === 'string' && s.resolvedUri.trim() !== '' ? (
          <details>
            <summary>Resolved Spotify URI</summary>
            <pre>{s.resolvedUri}</pre>
          </details>
        ) : null}
      </div>
    </div>
  );
}

type HistoryRecentResp = { ok: true; history: any[]; history_size: number };

function makeExportFilename(): string {
  const d = new Date();
  const pad = (n: number) => String(n).padStart(2, '0');
  const stamp = `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}-${pad(d.getHours())}${pad(d.getMinutes())}${pad(d.getSeconds())}`;
  return `tiptune-history-${stamp}.json`;
}

function downloadJson(filename: string, payload: any) {
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export function HistoryPage() {
  const [status, setStatus] = useState<'loading' | 'ok' | 'error'>('loading');
  const [err, setErr] = useState<string>('');
  const [items, setItems] = useState<any[]>([]);
  const [limit, setLimit] = useState<number | null>(null);
  const [historySize, setHistorySize] = useState<number>(1000);
  const [exporting, setExporting] = useState<boolean>(false);

  async function refresh() {
    setStatus((prev) => (prev === 'ok' ? 'loading' : prev));
    setErr('');
    try {
      const query = limit == null ? '' : `?limit=${encodeURIComponent(String(limit))}`;
      const j = await apiJson<HistoryRecentResp>(`/api/history/recent${query}`);
      setHistorySize(j.history_size);
      setLimit(limit == null ? j.history_size : Math.max(1, Math.min(limit, j.history_size)));
      const hist = Array.isArray(j?.history) ? j.history : [];
      setItems(hist.slice().reverse());
      setStatus('ok');
    } catch (e: any) {
      setErr(e?.message ? String(e.message) : String(e));
      setStatus('error');
    }
  }

  async function clearHistory() {
    setStatus((prev) => (prev === 'ok' ? 'loading' : prev));
    setErr('');
    try {
      const ok = window.confirm('Clear history? This will remove all saved history items.');
      if (!ok) {
        setStatus('ok');
        return;
      }
      await apiJson<{ ok: true }>(`/api/history/clear`, { method: 'POST' });
      await refresh();
    } catch (e: any) {
      setErr(e?.message ? String(e.message) : String(e));
      setStatus('error');
    }
  }

  async function exportHistory() {
    setErr('');
    setExporting(true);
    try {
      const j = await apiJson<HistoryRecentResp>('/api/history/recent');
      const hist = Array.isArray(j?.history) ? j.history : [];
      downloadJson(makeExportFilename(), { exported_at: new Date().toISOString(), history: hist });
    } catch (e: any) {
      setErr(e?.message ? String(e.message) : String(e));
      setStatus('error');
    } finally {
      setExporting(false);
    }
  }

  useEffect(() => {
    refresh().catch(() => {});
  }, []);

  return (
    <div className="page-events">
      <HeaderBar title="History" />

      <div className="row" style={{ marginTop: 12 }}>
        <button type="button" onClick={() => refresh()}>
          Refresh
        </button>
        <button type="button" onClick={() => clearHistory()} style={{ marginLeft: 8 }}>
          Clear
        </button>
        <button type="button" onClick={() => exportHistory()} style={{ marginLeft: 8 }} disabled={exporting}>
          {exporting ? 'Exporting…' : 'Export'}
        </button>
        <span className="muted" style={{ marginLeft: 10 }}>
          Recent song request processing results.
        </span>
      </div>

      <div className="row" style={{ marginTop: 12 }}>
        <label className="muted">Limit</label>
        <input
          type="number"
          min={1}
          max={historySize}
          value={String(limit ?? historySize)}
          onChange={(e) => setLimit(Math.max(1, Math.min(historySize, Number.parseInt(e.target.value || '0', 10) || historySize)))}
          style={{ width: 110, marginLeft: 8 }}
        />
        {status === 'loading' ? <span className="muted" style={{ marginLeft: 10 }}>Loading…</span> : null}
        {status === 'error' && err ? <span className="muted" style={{ marginLeft: 10 }}>{err}</span> : null}
      </div>

      <div className="out">
        {items.map((item, idx) => (
          <HistoryCard key={idx} item={item} />
        ))}
      </div>
    </div>
  );
}
