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
