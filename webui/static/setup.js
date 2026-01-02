function q(id) { return document.getElementById(id); }

async function apiJson(path, opts, timeoutMs) {
  const ctrl = new AbortController();
  const ms = (typeof timeoutMs === 'number' && timeoutMs > 0) ? timeoutMs : 8000;
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

q('openDashboardBtn').addEventListener('click', () => {
  window.location.href = '/settings?dashboard=1';
});

q('finishBtn').addEventListener('click', async () => {
  q('status').textContent = 'Saving...';
  try {
    await apiJson('/api/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ 'General': { setup_complete: 'true' } })
    });
    q('status').textContent = 'Setup marked complete.';
    window.location.href = '/';
  } catch (e) {
    q('status').textContent = 'Error: ' + (e && e.message ? e.message : String(e));
  }
});

async function refreshSpotifyStatus() {
  try {
    const st = await apiJson('/api/spotify/auth/status');
    const configured = !!st.configured;
    const authorized = !!st.authorized;
    const inProgress = !!st.in_progress;
    const authUrl = st.auth_url || '';
    const err = st.error || '';

    q('spRedirect').textContent = st.redirect_url || '(not set)';
    q('spStatus').textContent = authorized ? 'authorized' : configured ? (inProgress ? 'login in progress' : 'not authorized') : 'not configured';

    q('spConnectBtn').disabled = !configured || inProgress;
    q('spOpenBtn').style.display = (inProgress && authUrl) ? '' : 'none';

    if (err) {
      q('spMsg').textContent = 'Error: ' + String(err);
    }
  } catch (e) {
    q('spMsg').textContent = 'Error: ' + (e && e.message ? e.message : String(e));
  }
}

q('spRefreshBtn').addEventListener('click', () => {
  refreshSpotifyStatus();
});

q('spConnectBtn').addEventListener('click', async () => {
  q('spMsg').textContent = 'Starting Spotify login...';
  try {
    const data = await apiJson('/api/spotify/auth/start', { method: 'POST' });
    if (data && data.auth_url) {
      try { window.open(data.auth_url, '_blank', 'noopener,noreferrer'); } catch { window.location.href = data.auth_url; }
      q('spMsg').textContent = 'Browser opened. Complete Spotify login, then return here.';
    } else {
      q('spMsg').textContent = 'Error: missing auth_url';
    }
  } catch (e) {
    q('spMsg').textContent = 'Error: ' + (e && e.message ? e.message : String(e));
  } finally {
    refreshSpotifyStatus();
  }
});

q('spOpenBtn').addEventListener('click', async () => {
  try {
    const st = await apiJson('/api/spotify/auth/status');
    if (st && st.auth_url) {
      try { window.open(st.auth_url, '_blank', 'noopener,noreferrer'); } catch { window.location.href = st.auth_url; }
    }
  } catch (e) {
    q('spMsg').textContent = 'Error: ' + (e && e.message ? e.message : String(e));
  }
});

refreshSpotifyStatus();
setInterval(refreshSpotifyStatus, 1500);
