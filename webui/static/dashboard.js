function q(id) { return document.getElementById(id); }

async function apiJson(path, opts, timeoutMs) {
  const ctrl = new AbortController();
  const ms = (typeof timeoutMs === 'number' && timeoutMs > 0) ? timeoutMs : 5000;
  const tmr = setTimeout(() => ctrl.abort(), ms);
  try {
    const o = opts || {};
    const r = await fetch(path, { ...o, signal: ctrl.signal });
    const t = await r.text();
    let j;
    try { j = JSON.parse(t); } catch { j = { ok: false, error: t }; }
    if (!r.ok) { throw new Error(j.error || ('HTTP ' + r.status)); }
    if (j && j.ok === false) { throw new Error(j.error || 'Request failed'); }
    return j;
  } finally {
    clearTimeout(tmr);
  }
}

let lastQueueKey = null;
let lastNowPlayingKey = null;
let currentQueue = [];
let dragInProgress = false;
let queueOpInFlight = false;
let dragFromIndex = null;
let dropIndicator = null;
let dropInsertIndex = null;

function parseSpotifyTrackId(v) {
  try {
    const s = String(v || '');
    let m = s.match(/^spotify:track:([a-zA-Z0-9]+)$/);
    if (m) return m[1];
    m = s.match(/open\.spotify\.com\/track\/([a-zA-Z0-9]+)/);
    if (m) return m[1];
    return null;
  } catch (_) {
    return null;
  }
}

function truncateMiddle(s, maxLen) {
  const str = String(s || '');
  const n = (typeof maxLen === 'number' && maxLen > 0) ? maxLen : 40;
  if (str.length <= n) return str;
  const left = Math.max(1, Math.floor((n - 1) / 2));
  const right = Math.max(1, n - 1 - left);
  return str.slice(0, left) + '…' + str.slice(str.length - right);
}

function toDurationLabel(ms) {
  try {
    const n = Number(ms);
    if (!Number.isFinite(n) || n <= 0) return null;
    const s = Math.floor(n / 1000);
    const m = Math.floor(s / 60);
    const r = s % 60;
    return `${m}:${String(r).padStart(2, '0')}`;
  } catch (_) {
    return null;
  }
}

function makeQueueCard(item, idx, opts) {
  const o = (opts && typeof opts === 'object') ? opts : {};
  const allowDrag = (o.allowDrag !== false);
  const allowDelete = (o.allowDelete !== false);
  const indexLabel = (typeof o.indexLabel === 'string' && o.indexLabel.trim() !== '') ? o.indexLabel : `#${idx + 1}`;
  const extraClass = (typeof o.extraClass === 'string' && o.extraClass.trim() !== '') ? o.extraClass : null;
  const isObj = item && typeof item === 'object' && !Array.isArray(item);
  const uri = isObj ? String(item.uri || '') : String(item || '');
  const trackId = isObj ? (item.track_id ? String(item.track_id) : null) : parseSpotifyTrackId(uri);
  const name = isObj && typeof item.name === 'string' ? item.name : null;
  const artists = isObj && Array.isArray(item.artists) ? item.artists.filter(x => typeof x === 'string' && x.trim() !== '') : [];
  const album = isObj && typeof item.album === 'string' && item.album.trim() !== '' ? item.album : null;
  const duration = isObj ? toDurationLabel(item.duration_ms) : null;
  const explicit = isObj ? !!item.explicit : false;
  const spotifyUrl = isObj && typeof item.spotify_url === 'string' && item.spotify_url.trim() !== ''
    ? item.spotify_url
    : (trackId ? `https://open.spotify.com/track/${trackId}` : null);
  const artUrl = isObj && typeof item.album_image_url === 'string' && item.album_image_url.trim() !== '' ? item.album_image_url : null;

  const root = document.createElement('div');
  root.className = extraClass ? ('queueCard ' + extraClass) : 'queueCard';
  root.dataset.index = String(idx);

  const header = document.createElement('div');
  header.className = 'queueCardHeader';

  const leftWrap = document.createElement('div');
  leftWrap.className = 'queueCardHeaderLeft';

  const dragHandle = document.createElement('div');
  dragHandle.className = 'queueDragHandle';
  dragHandle.textContent = '⠿';
  dragHandle.setAttribute('draggable', allowDrag ? 'true' : 'false');
  if (!allowDrag) {
    dragHandle.style.opacity = '0.55';
    dragHandle.style.cursor = 'default';
  }

  const left = document.createElement('div');
  left.className = 'queueCardTitle';
  left.textContent = indexLabel;
  left.dataset.role = 'queueIndexLabel';

  leftWrap.appendChild(dragHandle);
  leftWrap.appendChild(left);

  const right = document.createElement('div');
  right.className = 'queueCardMeta';

  if (allowDelete) {
    const delBtn = document.createElement('button');
    delBtn.className = 'queueIconBtn';
    delBtn.type = 'button';
    delBtn.title = 'Remove from queue';
    delBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M3 6h18" stroke="currentColor" stroke-width="2" stroke-linecap="round"/><path d="M8 6v-2.5c0-.8.7-1.5 1.5-1.5h5C15.8 2 16.5 2.7 16.5 3.5V6" stroke="currentColor" stroke-width="2" stroke-linecap="round"/><path d="M6.5 6l1 16.5c.1.9.8 1.5 1.7 1.5h5.6c.9 0 1.6-.6 1.7-1.5l1-16.5" stroke="currentColor" stroke-width="2" stroke-linecap="round"/><path d="M10 11v7" stroke="currentColor" stroke-width="2" stroke-linecap="round"/><path d="M14 11v7" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>';
    delBtn.addEventListener('click', async (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      const label = name ? name : (trackId ? `spotify:track:${trackId}` : (uri || '(unknown)'));
      if (!confirm('Remove from queue?\n\n' + label)) return;
      const idxNow = getIndexFromCard(root);
      if (idxNow === null) return;
      await safeCall(() => deleteQueueIndex(idxNow));
    });
    right.appendChild(delBtn);
  }

  if (spotifyUrl) {
    const a = document.createElement('a');
    a.href = spotifyUrl;
    a.target = '_blank';
    a.rel = 'noreferrer noopener';
    a.textContent = 'Open in Spotify';
    right.appendChild(a);
  }

  if (trackId) {
    const idPill = document.createElement('span');
    idPill.className = 'pill';
    idPill.style.marginLeft = '8px';
    idPill.textContent = `track: ${truncateMiddle(trackId, 18)}`;
    right.appendChild(idPill);
  }

  header.appendChild(leftWrap);
  header.appendChild(right);
  root.appendChild(header);

  const body = document.createElement('div');
  body.className = 'queueCardBody';

  const titleRow = document.createElement('div');
  titleRow.style.display = 'flex';
  titleRow.style.gap = '10px';
  titleRow.style.alignItems = 'center';

  if (artUrl) {
    const img = document.createElement('img');
    img.className = 'queueArt';
    img.src = artUrl;
    img.alt = '';
    titleRow.appendChild(img);
  }

  const titleWrap = document.createElement('div');
  titleWrap.style.flex = '1';
  const main = document.createElement('div');
  main.className = 'queueTitleMain';
  main.textContent = name ? name : (trackId ? `spotify:track:${trackId}` : (uri || '(unknown)'));
  titleWrap.appendChild(main);

  const subParts = [];
  if (artists.length) subParts.push(artists.join(', '));
  if (album) subParts.push(album);
  if (subParts.length) {
    const sub = document.createElement('div');
    sub.className = 'queueTitleSub';
    sub.textContent = subParts.join(' · ');
    titleWrap.appendChild(sub);
  }

  titleRow.appendChild(titleWrap);
  body.appendChild(titleRow);

  const pills = document.createElement('div');
  pills.style.display = 'flex';
  pills.style.gap = '8px';
  pills.style.flexWrap = 'wrap';

  if (duration) {
    const p = document.createElement('span');
    p.className = 'pill';
    p.textContent = duration;
    pills.appendChild(p);
  }
  if (explicit) {
    const p = document.createElement('span');
    p.className = 'pill';
    p.textContent = 'Explicit';
    pills.appendChild(p);
  }
  if (pills.childNodes.length) body.appendChild(pills);

  const msg = document.createElement('div');
  msg.className = 'queueMessage';
  msg.textContent = uri;
  body.appendChild(msg);

  const details = document.createElement('details');
  const summary = document.createElement('summary');
  summary.textContent = 'Details';
  details.appendChild(summary);
  const pre = document.createElement('pre');
  pre.textContent = isObj ? JSON.stringify(item, null, 2) : uri;
  details.appendChild(pre);
  body.appendChild(details);

  root.appendChild(body);

  if (allowDrag) {
    dragHandle.addEventListener('dragstart', (ev) => {
      if (queueOpInFlight) {
        ev.preventDefault();
        return;
      }
      dragInProgress = true;
      const idxStart = getIndexFromCard(root);
      if (idxStart === null) {
        ev.preventDefault();
        return;
      }
      dragFromIndex = idxStart;
      try {
        ev.dataTransfer.setData('text/plain', String(idxStart));
        ev.dataTransfer.effectAllowed = 'move';
      } catch (_) {
      }
    });

    dragHandle.addEventListener('dragend', () => {
      dragInProgress = false;
      dragFromIndex = null;
      clearDropTargets();
    });
  }

  return root;
}

function clearDropTargets() {
  dropInsertIndex = null;
  if (dropIndicator && dropIndicator.parentNode) {
    dropIndicator.parentNode.removeChild(dropIndicator);
  }
  dropIndicator = null;
}

function ensureDropIndicator() {
  if (dropIndicator) return dropIndicator;
  dropIndicator = document.createElement('div');
  dropIndicator.className = 'queueInsertLine';
  return dropIndicator;
}

function computeInsertIndexFromEvent(ev) {
  const out = q('queueList');
  const cards = Array.from(out.querySelectorAll('.queueCard'));
  const y = ev.clientY;
  for (let i = 0; i < cards.length; i++) {
    const r = cards[i].getBoundingClientRect();
    const mid = r.top + (r.height / 2);
    if (y < mid) return i;
  }
  return cards.length;
}

function updateDropIndicator(insertIdx) {
  const out = q('queueList');
  const cards = Array.from(out.querySelectorAll('.queueCard'));
  const ind = ensureDropIndicator();
  if (insertIdx < 0) insertIdx = 0;
  if (insertIdx > cards.length) insertIdx = cards.length;
  if (insertIdx === cards.length) {
    out.appendChild(ind);
  } else {
    out.insertBefore(ind, cards[insertIdx]);
  }
  dropInsertIndex = insertIdx;
}

function getIndexFromCard(cardEl) {
  try {
    const out = q('queueList');
    const cards = Array.from(out.querySelectorAll('.queueCard'));
    const idx = cards.indexOf(cardEl);
    return idx >= 0 ? idx : null;
  } catch (_) {
    return null;
  }
}

function renderQueue(queued, force) {
  const arr = Array.isArray(queued) ? queued : [];
  const key = JSON.stringify(arr);
  if (!force && key === lastQueueKey) return;
  lastQueueKey = key;

  const out = q('queueList');
  out.innerHTML = '';
  if (!arr.length) {
    out.textContent = '(empty)';
    return;
  }
  for (let i = 0; i < arr.length; i++) {
    out.appendChild(makeQueueCard(arr[i], i));
  }
}

function renderNowPlaying(item, force) {
  const key = JSON.stringify(item || null);
  if (!force && key === lastNowPlayingKey) return;
  lastNowPlayingKey = key;

  const out = q('nowPlaying');
  out.innerHTML = '';
  if (!item) {
    out.textContent = '(none)';
    return;
  }

  out.appendChild(makeQueueCard(item, 0, {
    allowDrag: false,
    allowDelete: false,
    indexLabel: 'Now',
    extraClass: 'queueCardNowPlaying'
  }));
}

async function refreshQueue(opts) {
  const force = !!(opts && opts.force);
  const allowDuringOp = !!(opts && opts.allowDuringOp);
  const allowDuringDrag = !!(opts && opts.allowDuringDrag);
  if ((dragInProgress && !allowDuringDrag) || (queueOpInFlight && !allowDuringOp)) return;
  const data = await apiJson('/api/queue');
  const st = data.queue || {};
  const paused = !!st.paused;
  q('queueStatus').textContent = paused ? 'Paused' : 'Running';
  const nowPlaying = (st.now_playing_item && typeof st.now_playing_item === 'object')
    ? st.now_playing_item
    : (st.now_playing_track ? st.now_playing_track : null);
  currentQueue = (st.queued_items && st.queued_items.length) ? st.queued_items : (st.queued_tracks || []);
  renderNowPlaying(nowPlaying, force);
  renderQueue(currentQueue, force);
}

async function moveQueueIndex(fromIndex, toIndex) {
  if (queueOpInFlight) return;
  if (!Array.isArray(currentQueue)) return;
  if (!Number.isInteger(fromIndex) || !Number.isInteger(toIndex)) return;
  if (fromIndex === toIndex) return;
  if (fromIndex < 0 || fromIndex >= currentQueue.length) return;
  if (toIndex < 0 || toIndex >= currentQueue.length) return;
  queueOpInFlight = true;
  let opErr = null;
  try {
    const moved = currentQueue.splice(fromIndex, 1)[0];
    currentQueue.splice(toIndex, 0, moved);
    renderQueue(currentQueue, true);
    await apiJson('/api/queue/move', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ from_index: fromIndex, to_index: toIndex })
    });
  } catch (e) {
    opErr = e;
  } finally {
    await refreshQueue({ allowDuringOp: true, allowDuringDrag: true, force: true }).catch(() => {});
    queueOpInFlight = false;
  }
  if (opErr) throw opErr;
}

async function deleteQueueIndex(index) {
  if (queueOpInFlight) return;
  if (!Array.isArray(currentQueue)) return;
  if (!Number.isInteger(index)) return;
  if (index < 0 || index >= currentQueue.length) return;
  queueOpInFlight = true;
  let opErr = null;
  try {
    currentQueue.splice(index, 1);
    renderQueue(currentQueue, true);
    await apiJson('/api/queue/delete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ index })
    });
  } catch (e) {
    opErr = e;
  } finally {
    await refreshQueue({ allowDuringOp: true, allowDuringDrag: true, force: true }).catch(() => {});
    queueOpInFlight = false;
  }
  if (opErr) throw opErr;
}

q('refreshQueueBtn').addEventListener('click', () => refreshQueue().catch(err => console.error(err)));
q('setupBtn').addEventListener('click', () => { window.location.href = '/setup?rerun=1'; });
q('settingsBtn').addEventListener('click', () => {
  const isForced = (new URLSearchParams(window.location.search).get('dashboard') === '1');
  window.location.href = isForced ? '/settings?dashboard=1' : '/settings';
});
q('pauseBtn').addEventListener('click', async () => { await apiJson('/api/queue/pause', { method: 'POST' }); await refreshQueue(); });
q('resumeBtn').addEventListener('click', async () => { await apiJson('/api/queue/resume', { method: 'POST' }); await refreshQueue(); });

async function safeCall(fn, onErr) {
  try {
    await fn();
  } catch (e) {
    console.error(e);
    if (onErr) onErr(e);
  }
}

(async () => {
  await safeCall(refreshQueue, (e) => {
    q('queueStatus').textContent = 'Error';
    q('queueList').textContent = 'Error: ' + (e && e.message ? e.message : String(e));
  });
  setInterval(() => refreshQueue().catch(() => {}), 2000);
})();

q('queueList').addEventListener('dragover', (ev) => {
  if (!dragInProgress || queueOpInFlight) return;
  ev.preventDefault();
  const idx = computeInsertIndexFromEvent(ev);
  updateDropIndicator(idx);
});

q('queueList').addEventListener('dragleave', (ev) => {
  const out = q('queueList');
  const rel = ev.relatedTarget;
  if (rel && out.contains(rel)) return;
  clearDropTargets();
});

q('queueList').addEventListener('drop', async (ev) => {
  ev.preventDefault();
  if (queueOpInFlight) return;
  const fromRaw = (ev.dataTransfer && ev.dataTransfer.getData) ? ev.dataTransfer.getData('text/plain') : null;
  const fromIndex = (fromRaw !== null && fromRaw !== '') ? Number(fromRaw) : (Number.isInteger(dragFromIndex) ? dragFromIndex : NaN);
  const insertRaw = Number.isInteger(dropInsertIndex) ? dropInsertIndex : computeInsertIndexFromEvent(ev);
  clearDropTargets();

  if (!Number.isInteger(fromIndex)) return;
  if (!Array.isArray(currentQueue) || currentQueue.length < 2) return;

  let toIndex = insertRaw;
  if (fromIndex < toIndex) toIndex -= 1;
  if (toIndex < 0) toIndex = 0;
  if (toIndex >= currentQueue.length) toIndex = currentQueue.length - 1;

  await safeCall(() => moveQueueIndex(fromIndex, toIndex));
});
