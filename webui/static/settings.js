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

async function refreshCurrentDevice() {
  const data = await apiJson('/api/queue');
  const st = data.queue || {};
  const devName = st.playback_device_name || '';
  const devId = st.playback_device_id || '';
  q('currentDevice').textContent = devId ? ('Current: ' + (devName ? (devName + ' ') : '') + '(' + devId + ')') : 'Current: (none)';
}

async function refreshDevices() {
  const data = await apiJson('/api/spotify/devices');
  const sel = q('deviceSelect');
  sel.innerHTML = '';
  for (const d of (data.devices || [])) {
    const opt = document.createElement('option');
    opt.value = d.id || '';
    opt.textContent = (d.name || '(unknown)') + (d.is_active ? ' (active)' : '');
    sel.appendChild(opt);
  }
  try {
    const qst = await apiJson('/api/queue');
    const cur = (qst.queue || {}).playback_device_id;
    if (cur) sel.value = cur;
  } catch (e) {
  }
}

async function loadConfig() {
  const data = await apiJson('/api/config');
  const cfg = data.config || {};
  q('cfg_events_rpm').value = ((cfg['Events API'] || {}).max_requests_per_minute) || '';
  q('cfg_openai_model').value = ((cfg['OpenAI'] || {}).model) || '';
  q('cfg_spotify_client_id').value = ((cfg['Spotify'] || {}).client_id) || '';
  q('cfg_spotify_redirect_url').value = ((cfg['Spotify'] || {}).redirect_url) || '';
  q('cfg_google_cx').value = ((cfg['Search'] || {}).google_cx) || '';
  q('cfg_song_cost').value = ((cfg['General'] || {}).song_cost) || '';
  q('cfg_skip_cost').value = ((cfg['General'] || {}).skip_song_cost) || '';
  q('cfg_overlay_dur').value = ((cfg['General'] || {}).request_overlay_duration) || '';
  q('cfg_obs_enabled').value = (((cfg['OBS'] || {}).enabled) || 'true').toLowerCase();
}

q('dashboardBtn').addEventListener('click', () => {
  const isForced = (new URLSearchParams(window.location.search).get('dashboard') === '1');
  window.location.href = isForced ? '/?dashboard=1' : '/';
});
q('setupBtn').addEventListener('click', () => { window.location.href = '/setup?rerun=1'; });

q('refreshDevicesBtn').addEventListener('click', () => refreshDevices().catch(err => console.error(err)));
q('applyDeviceBtn').addEventListener('click', async () => {
  const deviceId = q('deviceSelect').value;
  await apiJson('/api/spotify/device', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ device_id: deviceId, persist: true }) });
  await refreshCurrentDevice();
  await refreshDevices();
});

q('saveConfigBtn').addEventListener('click', async () => {
  q('saveConfigStatus').textContent = 'Saving...';
  const payload = {
    'Events API': {
      url: q('cfg_events_url').value,
      max_requests_per_minute: q('cfg_events_rpm').value
    },
    'OpenAI': {
      api_key: q('cfg_openai_key').value,
      model: q('cfg_openai_model').value
    },
    'Spotify': {
      client_id: q('cfg_spotify_client_id').value,
      client_secret: q('cfg_spotify_client_secret').value,
      redirect_url: q('cfg_spotify_redirect_url').value
    },
    'Search': {
      google_api_key: q('cfg_google_key').value,
      google_cx: q('cfg_google_cx').value
    },
    'General': {
      song_cost: q('cfg_song_cost').value,
      skip_song_cost: q('cfg_skip_cost').value,
      request_overlay_duration: q('cfg_overlay_dur').value
    },
    'OBS': {
      enabled: q('cfg_obs_enabled').value
    }
  };
  try {
    await apiJson('/api/config', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    q('saveConfigStatus').textContent = 'Saved.';
    q('cfg_events_url').value = '';
    q('cfg_openai_key').value = '';
    q('cfg_spotify_client_secret').value = '';
    q('cfg_google_key').value = '';
    await loadConfig();
  } catch (e) {
    q('saveConfigStatus').textContent = 'Error: ' + e.message;
  }
});

async function safeCall(fn, onErr) {
  try {
    await fn();
  } catch (e) {
    console.error(e);
    if (onErr) onErr(e);
  }
}

(async () => {
  await Promise.all([
    safeCall(refreshCurrentDevice, (e) => {
      q('currentDevice').textContent = 'Error: ' + (e && e.message ? e.message : String(e));
    }),
    safeCall(refreshDevices, (e) => {
      q('currentDevice').textContent = 'Error: ' + (e && e.message ? e.message : String(e));
    }),
    safeCall(loadConfig, (e) => {
      q('saveConfigStatus').textContent = 'Error loading config: ' + (e && e.message ? e.message : String(e));
    })
  ]);
})();
