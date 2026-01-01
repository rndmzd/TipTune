import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';

import { apiJson, sseUrl } from '../api';

function safeParseJSON(s: string): any {
  try {
    return JSON.parse(s);
  } catch {
    return null;
  }
}

function get(obj: any, path: string[], fallback: any) {
  try {
    let cur = obj;
    for (const key of path) {
      if (!cur || typeof cur !== 'object' || !(key in cur)) return fallback;
      cur = cur[key];
    }
    return cur == null ? fallback : cur;
  } catch {
    return fallback;
  }
}

function toLocalTimeLabel(v: any): string | null {
  try {
    const d = v instanceof Date ? v : new Date(v);
    if (Number.isNaN(d.getTime())) return null;
    return d.toLocaleString();
  } catch {
    return null;
  }
}

function toEventTimestamp(item: any) {
  const ev = item && typeof item === 'object' ? item.event || item : null;
  const schemaDate = get(ev, ['timestamp', '$date'], null);
  if (schemaDate) return schemaDate;
  const ts = get(ev, ['timestamp'], null);
  if (typeof ts === 'string' || typeof ts === 'number') return ts;
  if (item && typeof item.ts === 'number') return item.ts * 1000;
  return null;
}

function summarize(item: any) {
  const ev = item && typeof item === 'object' ? item.event || item : null;
  const method = get(ev, ['method'], 'event');
  const subject = get(ev, ['object', 'subject'], null);
  const broadcaster = get(ev, ['object', 'broadcaster'], null);
  const id = get(ev, ['id'], get(ev, ['_id', '$oid'], null));
  const tokensRaw = get(ev, ['object', 'tip', 'tokens'], null);
  const tokens = typeof tokensRaw === 'number' ? tokensRaw : Number.isFinite(Number(tokensRaw)) ? Number(tokensRaw) : null;
  const isAnon = get(ev, ['object', 'tip', 'isAnon'], false);
  const userFromUserObj = get(ev, ['object', 'user', 'username'], null);
  const userFromMessage = get(ev, ['object', 'message', 'fromUser'], null);
  const username = isAnon ? 'Anonymous' : userFromUserObj || userFromMessage || 'Unknown';
  const tipMessage = get(ev, ['object', 'tip', 'message'], null);
  const chatMessage = get(ev, ['object', 'message', 'message'], null);
  const message = typeof tipMessage === 'string' && tipMessage.trim() !== '' ? tipMessage : chatMessage;
  const time = toLocalTimeLabel(toEventTimestamp(item));
  return { method, subject, broadcaster, id, tokens, username, message, time, ev };
}

function EventCard(props: { item: any }) {
  const s = useMemo(() => summarize(props.item), [props.item]);

  return (
    <div className="card">
      <div className="cardHeader">
        <div className="cardTitle">{`${s.method}${s.subject ? ` · ${s.subject}` : ''}`}</div>
        <div className="cardMeta">
          <span className="pill">{s.username}</span>
          {typeof s.broadcaster === 'string' && s.broadcaster.trim() !== '' ? <span className="pill" style={{ marginLeft: 8 }}>{s.broadcaster}</span> : null}
          {typeof s.tokens === 'number' ? <span className="pill pillStrong" style={{ marginLeft: 8 }}>{`${s.tokens} tokens`}</span> : null}
          {s.time ? <span style={{ marginLeft: 10 }}>{s.time}</span> : null}
          {typeof s.id === 'string' && s.id.trim() !== '' ? (
            <span className="pill" style={{ marginLeft: 8 }}>{`id: ${s.id.length > 12 ? `${s.id.slice(0, 8)}…` : s.id}`}</span>
          ) : null}
        </div>
      </div>
      <div className="cardBody">
        {typeof s.message === 'string' && s.message.trim() !== '' ? <div className="message">{s.message}</div> : null}
        <details>
          <summary>Details</summary>
          <pre>{JSON.stringify(props.item, null, 2)}</pre>
        </details>
      </div>
    </div>
  );
}

export function EventsPage() {
  const [lines, setLines] = useState<any[]>([]);

  useEffect(() => {
    apiJson('/api/events/recent?limit=50')
      .then((j: any) => {
        const evs = Array.isArray(j?.events) ? j.events : [];
        setLines(evs);
      })
      .catch(() => {});

    const es = new EventSource(sseUrl('/api/events/sse'));
    es.onmessage = (e) => {
      const parsed = safeParseJSON(e.data);
      setLines((prev) => {
        const next = [...prev, parsed && typeof parsed === 'object' ? parsed : { raw: e.data }];
        while (next.length > 300) next.shift();
        return next;
      });
    };
    es.onerror = () => {
      setLines((prev) => [...prev, { raw: '--- connection error ---' }]);
    };
    return () => {
      es.close();
    };
  }, []);

  return (
    <div className="page-events">
      <div className="row" style={{ justifyContent: 'space-between' }}>
        <h1 style={{ margin: 0, fontSize: 22 }}>Events</h1>
        <div className="muted">
          <Link to="/">Dashboard</Link>
        </div>
      </div>

      <div className="row" style={{ marginTop: 12 }}>
        <button type="button" onClick={() => setLines([])}>
          Clear
        </button>
        <span className="muted">Streaming Events API payloads via SSE.</span>
      </div>

      <div className="out">
        {lines.map((item, idx) => {
          if (item && typeof item === 'object' && 'raw' in item) {
            return (
              <div key={idx} className="card">
                <pre>{String((item as any).raw)}</pre>
              </div>
            );
          }
          return <EventCard key={idx} item={item} />;
        })}
      </div>
    </div>
  );
}
