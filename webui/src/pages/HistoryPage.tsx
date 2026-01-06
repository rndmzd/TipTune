import { useEffect, useMemo, useState } from 'react';

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
  };
}

function HistoryCard(props: { item: any }) {
  const s = useMemo(() => summarize(props.item), [props.item]);

  const titleParts: string[] = [];
  if (typeof s.status === 'string' && s.status.trim() !== '') titleParts.push(s.status);
  if (typeof s.songDetails === 'string' && s.songDetails.trim() !== '') {
    titleParts.push(s.songDetails);
  } else {
    const a = typeof s.artist === 'string' ? s.artist.trim() : '';
    const t = typeof s.song === 'string' ? s.song.trim() : '';
    const combined = [a, t].filter(Boolean).join(' - ');
    if (combined) titleParts.push(combined);
  }

  const title = titleParts.length ? titleParts.join(' · ') : 'request';

  const statusClass =
    s.status === 'added' ? 'pill pillInfo' : s.status === 'failed' ? 'pill pillWarn' : 'pill pillNeutral';

  return (
    <div className="card">
      <div className="cardHeader">
        <div className="cardTitle">{title}</div>
        <div className="cardMeta">
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
        <details>
          <summary>Details</summary>
          <pre>{JSON.stringify(props.item, null, 2)}</pre>
        </details>
      </div>
    </div>
  );
}

type HistoryRecentResp = { ok: true; history: any[] };

export function HistoryPage() {
  const [status, setStatus] = useState<'loading' | 'ok' | 'error'>('loading');
  const [err, setErr] = useState<string>('');
  const [items, setItems] = useState<any[]>([]);
  const [limit, setLimit] = useState<number>(100);

  async function refresh() {
    setStatus((prev) => (prev === 'ok' ? 'loading' : prev));
    setErr('');
    try {
      const j = await apiJson<HistoryRecentResp>(`/api/history/recent?limit=${encodeURIComponent(String(limit))}`);
      const hist = Array.isArray(j?.history) ? j.history : [];
      setItems(hist.slice().reverse());
      setStatus('ok');
    } catch (e: any) {
      setErr(e?.message ? String(e.message) : String(e));
      setStatus('error');
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
        <button type="button" onClick={() => setItems([])} style={{ marginLeft: 8 }}>
          Clear
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
          max={500}
          value={String(limit)}
          onChange={(e) => setLimit(Number.parseInt(e.target.value || '0', 10) || 100)}
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
