function q(id) { return document.getElementById(id); }
function safeParseJSON(s) {
  try { return JSON.parse(s); } catch (_) { return null; }
}
function get(obj, path, fallback) {
  try {
    let cur = obj;
    for (const key of path) {
      if (!cur || typeof cur !== 'object' || !(key in cur)) return fallback;
      cur = cur[key];
    }
    return cur == null ? fallback : cur;
  } catch (_) {
    return fallback;
  }
}
function toLocalTimeLabel(v) {
  try {
    const d = (v instanceof Date) ? v : new Date(v);
    if (isNaN(d.getTime())) return null;
    return d.toLocaleString();
  } catch (_) {
    return null;
  }
}
function toEventTimestamp(item) {
  const ev = item && typeof item === 'object' ? (item.event || item) : null;
  const schemaDate = get(ev, ['timestamp', '$date'], null);
  if (schemaDate) return schemaDate;

  const ts = get(ev, ['timestamp'], null);
  if (typeof ts === 'string' || typeof ts === 'number') return ts;

  if (item && typeof item.ts === 'number') return item.ts * 1000;
  return null;
}
function summarize(item) {
  const ev = item && typeof item === 'object' ? (item.event || item) : null;
  const method = get(ev, ['method'], 'event');
  const subject = get(ev, ['object', 'subject'], null);
  const broadcaster = get(ev, ['object', 'broadcaster'], null);
  const id = get(ev, ['id'], get(ev, ['_id', '$oid'], null));
  const tokensRaw = get(ev, ['object', 'tip', 'tokens'], null);
  const tokens = (typeof tokensRaw === 'number') ? tokensRaw : (Number.isFinite(Number(tokensRaw)) ? Number(tokensRaw) : null);
  const isAnon = get(ev, ['object', 'tip', 'isAnon'], false);
  const userFromUserObj = get(ev, ['object', 'user', 'username'], null);
  const userFromMessage = get(ev, ['object', 'message', 'fromUser'], null);
  const username = isAnon ? 'Anonymous' : (userFromUserObj || userFromMessage || 'Unknown');
  const tipMessage = get(ev, ['object', 'tip', 'message'], null);
  const chatMessage = get(ev, ['object', 'message', 'message'], null);
  const message = (typeof tipMessage === 'string' && tipMessage.trim() !== '') ? tipMessage : chatMessage;
  const time = toLocalTimeLabel(toEventTimestamp(item));
  return { method, subject, broadcaster, id, tokens, username, message, time, ev };
}
function makeCard(item) {
  const s = summarize(item);
  const root = document.createElement('div');
  root.className = 'card';

  const header = document.createElement('div');
  header.className = 'cardHeader';

  const left = document.createElement('div');
  left.className = 'cardTitle';
  left.textContent = `${s.method}${s.subject ? ' · ' + s.subject : ''}`;

  const right = document.createElement('div');
  right.className = 'cardMeta';

  const userPill = document.createElement('span');
  userPill.className = 'pill';
  userPill.textContent = s.username;
  right.appendChild(userPill);

  if (typeof s.broadcaster === 'string' && s.broadcaster.trim() !== '') {
    const b = document.createElement('span');
    b.className = 'pill';
    b.style.marginLeft = '8px';
    b.textContent = s.broadcaster;
    right.appendChild(b);
  }

  if (typeof s.tokens === 'number') {
    const tok = document.createElement('span');
    tok.className = 'pill pillStrong';
    tok.style.marginLeft = '8px';
    tok.textContent = `${s.tokens} tokens`;
    right.appendChild(tok);
  }
  if (s.time) {
    const t = document.createElement('span');
    t.style.marginLeft = '10px';
    t.textContent = s.time;
    right.appendChild(t);
  }

  if (typeof s.id === 'string' && s.id.trim() !== '') {
    const idPill = document.createElement('span');
    idPill.className = 'pill';
    idPill.style.marginLeft = '8px';
    const shortId = s.id.length > 12 ? (s.id.slice(0, 8) + '…') : s.id;
    idPill.textContent = `id: ${shortId}`;
    right.appendChild(idPill);
  }

  header.appendChild(left);
  header.appendChild(right);
  root.appendChild(header);

  const body = document.createElement('div');
  body.className = 'cardBody';

  if (typeof s.message === 'string' && s.message.trim() !== '') {
    const msg = document.createElement('div');
    msg.className = 'message';
    msg.textContent = s.message;
    body.appendChild(msg);
  }

  const details = document.createElement('details');
  const summaryEl = document.createElement('summary');
  summaryEl.textContent = 'Details';
  details.appendChild(summaryEl);
  const pre = document.createElement('pre');
  try {
    pre.textContent = JSON.stringify(item, null, 2);
  } catch (_) {
    pre.textContent = String(item);
  }
  details.appendChild(pre);
  body.appendChild(details);

  root.appendChild(body);
  return root;
}
function appendItem(item) {
  const out = q('out');
  out.appendChild(makeCard(item));
  while (out.children.length > 300) out.removeChild(out.firstChild);
  out.scrollTop = out.scrollHeight;
}
function appendTextLine(line) {
  const out = q('out');
  const root = document.createElement('div');
  root.className = 'card';
  const pre = document.createElement('pre');
  pre.textContent = line;
  root.appendChild(pre);
  out.appendChild(root);
  while (out.children.length > 300) out.removeChild(out.firstChild);
  out.scrollTop = out.scrollHeight;
}

q('clearBtn').addEventListener('click', () => { q('out').textContent = ''; });

fetch('/api/events/recent?limit=50').then(r => r.json()).then(j => {
  for (const ev of (j.events || [])) appendItem(ev);
}).catch(() => {});

const es = new EventSource('/api/events/sse');
es.onmessage = (e) => {
  const parsed = safeParseJSON(e.data);
  if (parsed && typeof parsed === 'object') return appendItem(parsed);
  appendTextLine(e.data);
};
es.onerror = () => {
  appendTextLine('--- connection error ---');
};
