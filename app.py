import asyncio
import configparser
import json
import logging
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

import httpx
from aiohttp import web

from chatdj.chatdj import SongRequest
from helpers.actions import Actions
from helpers.checks import Checks
from utils.structured_logging import get_structured_logger

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

config_path = Path(__file__).resolve().parent / 'config.ini'

config = configparser.ConfigParser()
config.read(config_path)

logger = get_structured_logger('mongobate.app')
shutdown_event: asyncio.Event = asyncio.Event()


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        v = value.strip().lower()
        if v in ('1', 'true', 'yes', 'y', 'on'):
            return True
        if v in ('0', 'false', 'no', 'n', 'off'):
            return False
    return default


def _is_secret_field(section: str, key: str) -> bool:
    k = (key or '').strip().lower()
    s = (section or '').strip().lower()
    if k in ('api_key', 'client_secret', 'google_api_key', 'password'):
        return True
    if s == 'events api' and k == 'url':
        return True
    if 'secret' in k or 'token' in k:
        return True
    return False


def _is_setup_complete() -> bool:
    try:
        if not config.has_section("General"):
            return False
        return config.getboolean("General", "setup_complete", fallback=False)
    except Exception:
        return False


def _update_ini_file(path: Path, updates: Dict[str, Dict[str, str]]) -> None:
    if not path.exists():
        example_path = path.with_name(path.name + '.example')
        try:
            if example_path.exists():
                path.write_text(example_path.read_text(encoding='utf-8', errors='replace'), encoding='utf-8')
            else:
                path.write_text('', encoding='utf-8')
        except Exception:
            path.write_text('', encoding='utf-8')

    lines = path.read_text(encoding='utf-8', errors='replace').splitlines(keepends=True)

    def find_section_bounds(section_name: str) -> Optional[tuple[int, int]]:
        header = f'[{section_name}]'
        start = None
        for idx, line in enumerate(lines):
            if line.strip() == header:
                start = idx
                break
        if start is None:
            return None
        end = len(lines)
        for idx in range(start + 1, len(lines)):
            if lines[idx].lstrip().startswith('[') and lines[idx].rstrip().endswith(']'):
                end = idx
                break
        return (start, end)

    for section, section_updates in updates.items():
        if not isinstance(section_updates, dict):
            continue

        bounds = find_section_bounds(section)
        if bounds is None:
            if lines and not lines[-1].endswith('\n'):
                lines[-1] = lines[-1] + '\n'
            if lines and lines[-1].strip() != '':
                lines.append('\n')
            lines.append(f'[{section}]\n')
            lines.append('\n')
            bounds = find_section_bounds(section)
            if bounds is None:
                continue

        section_start, section_end = bounds

        for key, value in section_updates.items():
            key_str = str(key)
            found_idx = None
            for idx in range(section_start + 1, section_end):
                line = lines[idx]
                stripped = line.strip()
                if stripped.startswith('#') or stripped.startswith(';') or stripped == '':
                    continue
                if '=' not in line and ':' not in line:
                    continue

                if '=' in line:
                    delim = '='
                else:
                    delim = ':'

                left, _right = line.split(delim, 1)
                if left.strip().lower() == key_str.strip().lower():
                    found_idx = idx
                    prefix = left.rstrip(' ') + delim
                    lines[idx] = f'{prefix} {value}\n'
                    break

            if found_idx is None:
                insert_at = section_end
                while insert_at > section_start + 1 and lines[insert_at - 1].strip() == '':
                    insert_at -= 1
                lines.insert(insert_at, f'{key_str} = {value}\n')
                section_end += 1

    path.write_text(''.join(lines), encoding='utf-8')


class WebUI:
    def __init__(self, service: 'SongRequestService', host: str, port: int):
        self._service = service
        self._host = host
        self._port = int(port)
        self._app = web.Application()
        self._runner: Optional[web.AppRunner] = None
        self._site: Optional[web.TCPSite] = None

        self._app.add_routes([
            web.get('/', self._page_dashboard),
            web.get('/setup', self._page_setup),
            web.get('/events', self._page_events),
            web.get('/api/queue', self._api_queue),
            web.post('/api/queue/pause', self._api_pause),
            web.post('/api/queue/resume', self._api_resume),
            web.get('/api/spotify/devices', self._api_devices),
            web.post('/api/spotify/device', self._api_set_device),
            web.get('/api/config', self._api_get_config),
            web.post('/api/config', self._api_update_config),
            web.get('/api/events/recent', self._api_events_recent),
            web.get('/api/events/sse', self._api_events_sse),
        ])

    async def start(self) -> None:
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, host=self._host, port=self._port)
        await self._site.start()

    async def stop(self) -> None:
        if self._runner:
            await self._runner.cleanup()
        self._runner = None
        self._site = None

    async def _page_dashboard(self, request: web.Request) -> web.Response:
        force_dashboard = _as_bool(request.query.get('dashboard'), default=False)
        if not force_dashboard and not _is_setup_complete():
            raise web.HTTPFound('/setup')

        html = """<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width,initial-scale=1\" />
  <title>TipTune</title>
  <style>
    body { font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif; margin: 24px; background: #0b1020; color: #e6e9f2; }
    *, *::before, *::after { box-sizing: border-box; }
    a { color: #8ab4ff; }
    .row { display: flex; gap: 16px; flex-wrap: wrap; }
    .card { background: #121a33; border: 1px solid #1e2a4d; border-radius: 10px; padding: 16px; min-width: 320px; flex: 1; }
    h1 { margin: 0 0 12px 0; font-size: 22px; }
    h2 { margin: 0 0 12px 0; font-size: 16px; }
    label { display: block; font-size: 12px; opacity: 0.9; margin-top: 10px; }
    input, select { width: 100%; padding: 10px; border-radius: 8px; border: 1px solid #2a3a66; background: #0e1530; color: #e6e9f2; }
    button { padding: 10px 12px; border-radius: 8px; border: 1px solid #2a3a66; background: #1b2a55; color: #e6e9f2; cursor: pointer; }
    button:hover { background: #23366f; }
    .pill { display: inline-block; padding: 3px 8px; border-radius: 999px; border: 1px solid #2a3a66; background: #0e1530; font-size: 12px; }
    pre { white-space: pre-wrap; word-break: break-word; background: #0e1530; border: 1px solid #2a3a66; padding: 10px; border-radius: 8px; max-height: 240px; overflow: auto; }
    .muted { opacity: 0.8; font-size: 12px; }
    .actions { display: flex; gap: 10px; align-items: center; margin-top: 10px; flex-wrap: wrap; }
    .actions + .muted { margin-top: 8px; }
  </style>
</head>
<body>
  <div class=\"actions\" style=\"justify-content: space-between\"> 
    <h1>TipTune</h1>
    <div style=\"display:flex; gap:10px; align-items:center;\">
      <button id=\"setupBtn\" type=\"button\">Setup Wizard</button>
      <div class=\"muted\"><a href=\"/events\">Events</a></div>
    </div>
  </div>

  <div class=\"row\">
    <div class=\"card\">
      <h2>Queue</h2>
      <div class=\"actions\">
        <span id=\"queueStatus\" class=\"pill\">Loading...</span>
        <button id=\"pauseBtn\" type=\"button\">Pause</button>
        <button id=\"resumeBtn\" type=\"button\">Resume</button>
        <button id=\"refreshQueueBtn\" type=\"button\">Refresh</button>
      </div>
      <label>Queued tracks</label>
      <pre id=\"queueList\">(loading)</pre>
      <div class=\"muted\">Queue is the in-memory AutoDJ queue (URIs). Current song plays to completion when paused.</div>
    </div>

    <div class=\"card\">
      <h2>Playback Device</h2>
      <div class=\"muted\" id=\"currentDevice\">Loading...</div>
      <label for=\"deviceSelect\">Available devices</label>
      <select id=\"deviceSelect\"></select>
      <div class=\"actions\">
        <button id=\"refreshDevicesBtn\" type=\"button\">Refresh</button>
        <button id=\"applyDeviceBtn\" type=\"button\">Apply + Save</button>
      </div>
      <div class=\"muted\">Applies Spotify transfer playback and saves the chosen device id to config.ini.</div>
    </div>
  </div>

  <div class=\"card\" style=\"margin-top: 16px\">
    <h2>Settings</h2>
    <div class=\"muted\">Secret fields are not shown. Leave secret fields blank to keep the existing value.</div>
    <div class=\"row\" style=\"margin-top: 8px\">
      <div style=\"flex: 1; min-width: 320px\">
        <label>Events API URL (secret)</label>
        <input id=\"cfg_events_url\" type=\"password\" placeholder=\"(leave blank to keep)\" />
        <label>Events API max_requests_per_minute</label>
        <input id=\"cfg_events_rpm\" type=\"text\" />
        <label>OpenAI API key (secret)</label>
        <input id=\"cfg_openai_key\" type=\"password\" placeholder=\"(leave blank to keep)\" />
        <label>OpenAI model</label>
        <input id=\"cfg_openai_model\" type=\"text\" />
      </div>
      <div style=\"flex: 1; min-width: 320px\">
        <label>Spotify client_id</label>
        <input id=\"cfg_spotify_client_id\" type=\"text\" />
        <label>Spotify client_secret (secret)</label>
        <input id=\"cfg_spotify_client_secret\" type=\"password\" placeholder=\"(leave blank to keep)\" />
        <label>Spotify redirect_url</label>
        <input id=\"cfg_spotify_redirect_url\" type=\"text\" />
        <label>OBS enabled</label>
        <select id=\"cfg_obs_enabled\">
          <option value=\"true\">true</option>
          <option value=\"false\">false</option>
        </select>
      </div>
      <div style=\"flex: 1; min-width: 320px\">
        <label>Search google_api_key (secret)</label>
        <input id=\"cfg_google_key\" type=\"password\" placeholder=\"(leave blank to keep)\" />
        <label>Search google_cx</label>
        <input id=\"cfg_google_cx\" type=\"text\" />
        <label>General song_cost</label>
        <input id=\"cfg_song_cost\" type=\"text\" />
        <label>General skip_song_cost</label>
        <input id=\"cfg_skip_cost\" type=\"text\" />
        <label>General request_overlay_duration</label>
        <input id=\"cfg_overlay_dur\" type=\"text\" />
      </div>
    </div>
    <div class=\"actions\">
      <button id=\"saveConfigBtn\" type=\"button\">Save Settings</button>
      <span id=\"saveConfigStatus\" class=\"muted\"></span>
    </div>
  </div>

  <script>
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

    async function refreshQueue() {
      const data = await apiJson('/api/queue');
      const st = data.queue || {};
      const paused = !!st.paused;
      q('queueStatus').textContent = paused ? 'Paused' : 'Running';
      q('queueList').textContent = (st.queued_tracks && st.queued_tracks.length) ? st.queued_tracks.join('\\\\n') : '(empty)';
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

    q('refreshQueueBtn').addEventListener('click', () => refreshQueue().catch(err => console.error(err)));
    q('setupBtn').addEventListener('click', () => { window.location.href = '/setup?rerun=1'; });
    q('pauseBtn').addEventListener('click', async () => { await apiJson('/api/queue/pause', { method: 'POST' }); await refreshQueue(); });
    q('resumeBtn').addEventListener('click', async () => { await apiJson('/api/queue/resume', { method: 'POST' }); await refreshQueue(); });

    q('refreshDevicesBtn').addEventListener('click', () => refreshDevices().catch(err => console.error(err)));
    q('applyDeviceBtn').addEventListener('click', async () => {
      const deviceId = q('deviceSelect').value;
      await apiJson('/api/spotify/device', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ device_id: deviceId, persist: true }) });
      await refreshQueue();
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
        safeCall(refreshQueue, (e) => {
          q('queueStatus').textContent = 'Error';
          q('queueList').textContent = 'Error: ' + (e && e.message ? e.message : String(e));
          q('currentDevice').textContent = 'Error: ' + (e && e.message ? e.message : String(e));
        }),
        safeCall(refreshDevices, (e) => {
          q('currentDevice').textContent = 'Error: ' + (e && e.message ? e.message : String(e));
        }),
        safeCall(loadConfig, (e) => {
          q('saveConfigStatus').textContent = 'Error loading config: ' + (e && e.message ? e.message : String(e));
        })
      ]);
      setInterval(() => refreshQueue().catch(() => {}), 2000);
    })();
  </script>
</body>
</html>"""
        return web.Response(text=html, content_type='text/html', headers={"Cache-Control": "no-store"})

    async def _page_setup(self, request: web.Request) -> web.Response:
        rerun = _as_bool(request.query.get('rerun'), default=False)
        is_complete = _is_setup_complete()
        status_text = "complete" if is_complete else "incomplete"
        title_suffix = " (rerun)" if rerun else ""
        html = f"""<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width,initial-scale=1\" />
  <title>TipTune Setup</title>
  <style>
    body {{ font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif; margin: 24px; background: #0b1020; color: #e6e9f2; }}
    *, *::before, *::after {{ box-sizing: border-box; }}
    a {{ color: #8ab4ff; }}
    .row {{ display: flex; gap: 16px; flex-wrap: wrap; }}
    .card {{ background: #121a33; border: 1px solid #1e2a4d; border-radius: 10px; padding: 16px; min-width: 320px; flex: 1; }}
    h1 {{ margin: 0 0 12px 0; font-size: 22px; }}
    h2 {{ margin: 0 0 12px 0; font-size: 16px; }}
    button {{ padding: 10px 12px; border-radius: 8px; border: 1px solid #2a3a66; background: #1b2a55; color: #e6e9f2; cursor: pointer; }}
    button:hover {{ background: #23366f; }}
    .muted {{ opacity: 0.8; font-size: 12px; }}
    .actions {{ display: flex; gap: 10px; align-items: center; margin-top: 10px; flex-wrap: wrap; }}
    .pill {{ display: inline-block; padding: 3px 8px; border-radius: 999px; border: 1px solid #2a3a66; background: #0e1530; font-size: 12px; }}
  </style>
</head>
<body>
  <div class=\"actions\" style=\"justify-content: space-between\">
    <h1>Setup Wizard{title_suffix}</h1>
    <div class=\"muted\"><a href=\"/?dashboard=1\">Dashboard</a></div>
  </div>

  <div class=\"card\">
    <h2>Setup status: <span class=\"pill\">{status_text}</span></h2>
    <div class=\"muted\">Use the dashboard to enter your settings (for now). When you're done, mark setup as complete.</div>
    <div class=\"actions\">
      <button id=\"openDashboardBtn\" type=\"button\">Open Dashboard Settings</button>
      <button id=\"finishBtn\" type=\"button\">Mark Setup Complete</button>
    </div>
    <div id=\"status\" class=\"muted\"></div>
  </div>

  <script>
    function q(id) {{ return document.getElementById(id); }}

    async function apiJson(path, opts, timeoutMs) {{
      const ctrl = new AbortController();
      const ms = (typeof timeoutMs === 'number' && timeoutMs > 0) ? timeoutMs : 8000;
      const tmr = setTimeout(() => ctrl.abort(), ms);
      try {{
        const o = opts || {{}};
        const r = await fetch(path, {{ ...o, signal: ctrl.signal }});
        const t = await r.text();
        let j;
        try {{ j = JSON.parse(t); }} catch {{ j = {{ ok: false, error: t }}; }}
        if (!r.ok) {{ throw new Error(j.error || ('HTTP ' + r.status)); }}
        if (j && j.ok === false) {{ throw new Error(j.error || 'Request failed'); }}
        return j;
      }} finally {{
        clearTimeout(tmr);
      }}
    }}

    q('openDashboardBtn').addEventListener('click', () => {{
      window.location.href = '/?dashboard=1';
    }});

    q('finishBtn').addEventListener('click', async () => {{
      q('status').textContent = 'Saving...';
      try {{
        await apiJson('/api/config', {{
          method: 'POST',
          headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify({{ 'General': {{ setup_complete: 'true' }} }})
        }});
        q('status').textContent = 'Setup marked complete.';
        window.location.href = '/';
      }} catch (e) {{
        q('status').textContent = 'Error: ' + (e && e.message ? e.message : String(e));
      }}
    }});
  </script>
</body>
</html>"""
        return web.Response(text=html, content_type='text/html', headers={"Cache-Control": "no-store"})

    async def _page_events(self, _request: web.Request) -> web.Response:
        html = """<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width,initial-scale=1\" />
  <title>TipTune Events</title>
  <style>
    body { font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif; margin: 24px; background: #0b1020; color: #e6e9f2; }
    a { color: #8ab4ff; }
    .out { max-height: 70vh; overflow: auto; margin-top: 12px; display: flex; flex-direction: column; gap: 10px; }
    .row { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
    button { padding: 10px 12px; border-radius: 8px; border: 1px solid #2a3a66; background: #1b2a55; color: #e6e9f2; cursor: pointer; }
    button:hover { background: #23366f; }
    .muted { opacity: 0.8; font-size: 12px; }
    .card { background: #0e1530; border: 1px solid #2a3a66; border-radius: 10px; padding: 10px 12px; }
    .cardHeader { display: flex; gap: 10px; align-items: baseline; justify-content: space-between; flex-wrap: wrap; }
    .cardTitle { font-weight: 650; font-size: 13px; }
    .cardMeta { opacity: 0.85; font-size: 12px; }
    .pill { display: inline-block; padding: 2px 8px; border-radius: 999px; border: 1px solid #2a3a66; background: rgba(35, 54, 111, 0.35); font-size: 12px; }
    .pillStrong { border-color: #4b69c8; background: rgba(74, 105, 200, 0.22); }
    .cardBody { margin-top: 8px; display: flex; flex-direction: column; gap: 6px; }
    .message { white-space: pre-wrap; word-break: break-word; line-height: 1.35; }
    details { margin-top: 8px; }
    summary { cursor: pointer; user-select: none; opacity: 0.9; }
    pre { white-space: pre-wrap; word-break: break-word; background: #0b1020; border: 1px solid #2a3a66; padding: 10px; border-radius: 8px; overflow: auto; }
  </style>
</head>
<body>
  <div class=\"row\" style=\"justify-content: space-between\"> 
    <h1 style=\"margin:0;font-size:22px\">Events</h1>
    <div class=\"muted\"><a href=\"/\">Dashboard</a></div>
  </div>
  <div class=\"row\" style=\"margin-top:12px\">
    <button id=\"clearBtn\" type=\"button\">Clear</button>
    <span class=\"muted\">Streaming Events API payloads via SSE.</span>
  </div>
  <div id=\"out\" class=\"out\"></div>
  <script>
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
      const summary = document.createElement('summary');
      summary.textContent = 'Details';
      details.appendChild(summary);
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
  </script>
</body>
</html>"""
        return web.Response(text=html, content_type='text/html', headers={"Cache-Control": "no-store"})

    async def _api_queue(self, _request: web.Request) -> web.Response:
        try:
            queue = await self._service.get_queue_state()
            return web.json_response({"ok": True, "queue": queue})
        except Exception as exc:
            logger.exception("webui.api.queue.error", exc=exc, message="Failed to get queue state")
            return web.json_response({"ok": False, "error": str(exc)})

    async def _api_pause(self, _request: web.Request) -> web.Response:
        ok = await self._service.pause_queue()
        return web.json_response({"ok": bool(ok)})

    async def _api_resume(self, _request: web.Request) -> web.Response:
        ok = await self._service.resume_queue()
        return web.json_response({"ok": bool(ok)})

    async def _api_devices(self, _request: web.Request) -> web.Response:
        try:
            devices = await self._service.get_devices()
            return web.json_response({"ok": True, "devices": devices})
        except Exception as exc:
            logger.exception("webui.api.devices.error", exc=exc, message="Failed to get devices")
            return web.json_response({"ok": False, "error": str(exc), "devices": []})

    async def _api_set_device(self, request: web.Request) -> web.Response:
        try:
            payload = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "Invalid JSON"}, status=400)

        device_id = payload.get('device_id') if isinstance(payload, dict) else None
        persist = _as_bool(payload.get('persist'), default=True) if isinstance(payload, dict) else True
        ok = await self._service.set_device(device_id, persist=persist)
        if not ok:
            return web.json_response({"ok": False, "error": "Failed to set device"}, status=400)
        return web.json_response({"ok": True})

    async def _api_get_config(self, _request: web.Request) -> web.Response:
        try:
            return web.json_response({"ok": True, "config": self._service.get_config_for_ui()})
        except Exception as exc:
            logger.exception("webui.api.config.error", exc=exc, message="Failed to read config for UI")
            return web.json_response({"ok": False, "error": str(exc), "config": {}})

    async def _api_update_config(self, request: web.Request) -> web.Response:
        try:
            payload = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "Invalid JSON"}, status=400)

        ok, error = await self._service.update_config_from_ui(payload)
        if not ok:
            return web.json_response({"ok": False, "error": error or "update failed"}, status=400)
        return web.json_response({"ok": True})

    async def _api_events_recent(self, request: web.Request) -> web.Response:
        limit_raw = request.query.get('limit', '50')
        try:
            limit = max(1, min(500, int(limit_raw)))
        except Exception:
            limit = 50
        return web.json_response({"ok": True, "events": self._service.get_recent_events(limit=limit)})

    async def _api_events_sse(self, request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse(status=200, reason='OK', headers={
            'Content-Type': 'text/event-stream',
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
        })
        await resp.prepare(request)

        q_events = self._service.register_events_subscriber()

        try:
            try:
                await resp.write(b': connected\n\n')
            except (ConnectionResetError, BrokenPipeError):
                return resp

            while True:
                try:
                    item = await asyncio.wait_for(q_events.get(), timeout=15)
                except asyncio.TimeoutError:
                    transport = request.transport
                    if transport is None or transport.is_closing():
                        break
                    try:
                        await resp.write(b': ping\n\n')
                    except (ConnectionResetError, BrokenPipeError):
                        break
                    continue

                transport = request.transport
                if transport is None or transport.is_closing():
                    break

                data = json.dumps(item, default=str)
                try:
                    await resp.write(f'data: {data}\n\n'.encode('utf-8'))
                except (ConnectionResetError, BrokenPipeError):
                    break
            return resp
        except asyncio.CancelledError:
            return resp
        except Exception:
            return resp
        finally:
            self._service.unregister_events_subscriber(q_events)
            try:
                await resp.write_eof()
            except (ConnectionResetError, BrokenPipeError):
                pass


def handle_exception(_loop, context):
    if shutdown_event.is_set():
        return
    msg = context.get("exception", context.get("message"))
    logger.error("app.error",
                 message="Caught exception in event loop",
                 data={"error": str(msg)})


class EventsAPIClient:
    def __init__(self, start_url: str, max_requests_per_minute: int = 1000):
        self._next_url = start_url
        rpm = max(1, int(max_requests_per_minute))
        self._poll_interval_seconds = 60 / (rpm / 10)

    @property
    def poll_interval_seconds(self) -> float:
        return self._poll_interval_seconds

    async def poll(self, client: httpx.AsyncClient) -> list[dict]:
        resp = await client.get(self._next_url, timeout=30)
        resp.raise_for_status()
        payload = resp.json()

        events = payload.get("events", [])
        if isinstance(events, list):
            self._next_url = payload.get("nextUrl", self._next_url)
            return events

        return []


class SongRequestService:
    def __init__(self):
        self.checks = Checks()

        obs_enabled = config.getboolean("OBS", "enabled", fallback=True) if config.has_section("OBS") else False
        self.actions = Actions(
            chatdj=True,
            obs_integration=obs_enabled
        )

        self._tip_queue: asyncio.Queue[Dict[str, Any]] = asyncio.Queue(maxsize=1000)

        self._stop_event = asyncio.Event()
        self._tasks: list[asyncio.Task] = []

        self._events_recent: list[dict] = []
        self._events_recent_max = 500
        self._events_subscribers: set[asyncio.Queue] = set()

        self._web: Optional[WebUI] = None

    async def start(self) -> None:
        self._tasks.append(asyncio.create_task(self._events_loop()))
        self._tasks.append(asyncio.create_task(self._tip_processor_loop()))
        self._tasks.append(asyncio.create_task(self._queue_watchdog()))
        self._tasks.append(asyncio.create_task(self._local_control_loop()))

        web_host = config.get("Web", "host", fallback="127.0.0.1") if config.has_section("Web") else "127.0.0.1"
        web_port = config.getint("Web", "port", fallback=8765) if config.has_section("Web") else 8765
        try:
            self._web = WebUI(self, host=web_host, port=web_port)
            await self._web.start()
            logger.info("webui.started", message="Web UI started", data={"host": web_host, "port": web_port})
        except Exception as exc:
            logger.exception("webui.error", exc=exc, message="Failed to start Web UI")

    async def stop(self) -> None:
        self._stop_event.set()
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

        if self._web:
            try:
                await self._web.stop()
            except Exception:
                pass
            self._web = None

        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self.actions.auto_dj.check_queue_status, True)
            if getattr(self.actions.auto_dj, 'queued_tracks', []):
                await loop.run_in_executor(None, self.actions.auto_dj.clear_playback_context, True)
        except Exception:
            pass

    async def _queue_watchdog(self) -> None:
        if not getattr(self.actions, 'chatdj_enabled', False):
            return
        if not hasattr(self.actions, 'auto_dj'):
            return
        while not self._stop_event.is_set():
            try:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, self.actions.auto_dj.check_queue_status)
            except Exception as exc:
                logger.exception("song.queue.check.error", exc=exc, message="Queue watchdog error")

            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=5)
            except asyncio.TimeoutError:
                pass

    async def _local_control_loop(self) -> None:
        if not getattr(self.actions, 'chatdj_enabled', False):
            return
        if not hasattr(self.actions, 'auto_dj'):
            return

        logger.info(
            "local.control.ready",
            message="Local controls enabled. Type 'pause' or 'resume' in this console to pause/unpause the queue.",
        )

        buf = ""
        loop = asyncio.get_running_loop()
        is_windows = (os.name == 'nt')

        if is_windows:
            try:
                import msvcrt  # type: ignore
            except Exception:
                is_windows = False

        while not self._stop_event.is_set():
            try:
                if is_windows:
                    if msvcrt.kbhit():
                        ch = msvcrt.getwch()
                        if ch in ('\r', '\n'):
                            sys.stdout.write("\n")
                            sys.stdout.flush()
                            cmd = buf.strip().lower()
                            buf = ""
                            await self._handle_local_command(cmd, loop)
                        elif ch == '\x03':
                            shutdown_event.set()
                            break
                        elif ch == '\b':
                            buf = buf[:-1]
                            sys.stdout.write("\b \b")
                            sys.stdout.flush()
                        else:
                            buf += ch
                            sys.stdout.write(ch)
                            sys.stdout.flush()
                    else:
                        await asyncio.sleep(0.1)
                    continue

                line = await loop.run_in_executor(None, sys.stdin.readline)
                if line == "":
                    await asyncio.sleep(0.25)
                    continue
                cmd = line.strip().lower()
                await self._handle_local_command(cmd, loop)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.exception("local.control.error", exc=exc, message="Local control loop error")
                await asyncio.sleep(1)

    async def _handle_local_command(self, cmd: str, loop: asyncio.AbstractEventLoop) -> None:
        if cmd in ("pause", "p"):
            await loop.run_in_executor(None, self.actions.auto_dj.pause_queue)
            return
        if cmd in ("resume", "unpause", "r"):
            await loop.run_in_executor(None, self.actions.auto_dj.unpause_queue)
            return
        if cmd in ("status", "s"):
            paused = await loop.run_in_executor(None, self.actions.auto_dj.queue_paused)
            queued = len(getattr(self.actions.auto_dj, 'queued_tracks', []))
            logger.info(
                "local.control.status",
                message="Queue status.",
                data={"paused": paused, "queued_tracks": queued}
            )
            return
        if cmd in ("help", "?"):
            logger.info(
                "local.control.help",
                message="Local commands: pause | resume | status | help"
            )
            return

    async def _events_loop(self) -> None:
        api: Optional[EventsAPIClient] = None
        api_url: Optional[str] = None
        api_rpm: Optional[int] = None

        async with httpx.AsyncClient() as client:
            while not self._stop_event.is_set():
                try:
                    events_api_url = config.get("Events API", "url", fallback="").strip()
                    max_rpm = config.getint("Events API", "max_requests_per_minute", fallback=1000)
                except Exception:
                    events_api_url = ""
                    max_rpm = 1000

                if not events_api_url:
                    api = None
                    api_url = None
                    api_rpm = None
                    try:
                        await asyncio.wait_for(self._stop_event.wait(), timeout=5)
                    except asyncio.TimeoutError:
                        pass
                    continue

                if api is None or api_url != events_api_url or api_rpm != max_rpm:
                    api = EventsAPIClient(events_api_url, max_requests_per_minute=max_rpm)
                    api_url = events_api_url
                    api_rpm = max_rpm

                try:
                    events = await api.poll(client)
                    for event in events:
                        self.publish_events_api_event(event)
                        await self._handle_event(event)
                except asyncio.CancelledError:
                    break
                except Exception as exc:
                    logger.exception("events_api.poll.error", exc=exc, message="Failed to poll Events API")

                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=api.poll_interval_seconds)
                except asyncio.TimeoutError:
                    pass

    async def _handle_event(self, event: Dict[str, Any]) -> None:
        if not isinstance(event, dict):
            return

        method = event.get('method')
        if method != 'tip':
            return

        tip_obj = event.get('object') if isinstance(event.get('object'), dict) else event
        await self._tip_queue.put(tip_obj)

    async def _tip_processor_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                tip_obj = await asyncio.wait_for(self._tip_queue.get(), timeout=1)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            try:
                await self._handle_tip(tip_obj)
            except Exception as exc:
                logger.exception("tip.queue.process.error", exc=exc, message="Error processing queued tip")
            finally:
                self._tip_queue.task_done()

    async def _handle_tip(self, event: Dict[str, Any]) -> None:
        try:
            tip_amount = event.get('tip', {}).get('tokens', 0)
            tip_message = event.get('tip', {}).get('message', '').strip()
            username = event.get('user', {}).get('username', 'Anonymous')

            if not isinstance(tip_amount, int) or tip_amount <= 0:
                return

            is_song_request = self.checks.is_song_request(tip_amount)
            is_skip_request = self.checks.is_skip_song_request(tip_amount)

            if not is_song_request and is_skip_request:
                skipped = await self.actions.skip_song()
                if not skipped:
                    await self.actions.trigger_warning_overlay(
                        username,
                        "Couldn't skip the current song.",
                        10
                    )
                return

            if not is_song_request:
                return

            request_count = max(1, self.checks.get_request_count(tip_amount))

            if tip_message == "":
                await self.actions.trigger_warning_overlay(
                    username,
                    "Couldn't identify a song in your tip, because the tip note was blank. It may have been removed due to blocked words.",
                    10
                )
                return

            if len(tip_message) < 3:
                tip_message = f"The song name might be \"{tip_message}\"."

            song_extracts = await self.actions.extract_song_titles(tip_message, request_count)

            if not song_extracts:
                song_extracts = [SongRequest(song=tip_message, artist="", spotify_uri=None)]

            for song_info in song_extracts:
                song_uri: Optional[str]
                if getattr(song_info, 'spotify_uri', None):
                    song_uri = song_info.spotify_uri
                else:
                    song_uri = await self.actions.find_song_spotify(song_info)

                if not song_uri:
                    await self.actions.trigger_warning_overlay(
                        username,
                        "Couldn't find song on Spotify. Did you include artist and song name?",
                        10
                    )
                    continue

                if not await self.actions.available_in_market(song_uri):
                    await self.actions.trigger_warning_overlay(
                        username,
                        "Requested song not available in US market.",
                        10
                    )
                    continue

                song_details = f"{song_info.artist} - {song_info.song}".strip()
                await self.actions.add_song_to_queue(song_uri, username, song_details)

        except Exception as exc:
            logger.exception("event.tip.error", exc=exc, message="Error processing tip event")

    def publish_events_api_event(self, event: Dict[str, Any]) -> None:
        if not isinstance(event, dict):
            return
        item = {
            "ts": time.time(),
            "event": event
        }

        self._events_recent.append(item)
        if len(self._events_recent) > self._events_recent_max:
            self._events_recent = self._events_recent[-self._events_recent_max:]

        for q in list(self._events_subscribers):
            try:
                q.put_nowait(item)
            except asyncio.QueueFull:
                try:
                    _ = q.get_nowait()
                except Exception:
                    pass
                try:
                    q.put_nowait(item)
                except Exception:
                    pass
            except Exception:
                pass

    def register_events_subscriber(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._events_subscribers.add(q)
        return q

    def unregister_events_subscriber(self, q: asyncio.Queue) -> None:
        try:
            self._events_subscribers.discard(q)
        except Exception:
            pass

    def get_recent_events(self, limit: int = 50) -> list[dict]:
        if limit <= 0:
            return []
        return self._events_recent[-limit:]

    async def get_queue_state(self) -> Dict[str, Any]:
        if not getattr(self.actions, 'chatdj_enabled', False):
            return {"enabled": False}
        if not hasattr(self.actions, 'auto_dj'):
            return {"enabled": False}

        queued_tracks = list(getattr(self.actions.auto_dj, 'queued_tracks', []))
        playback_device_id = getattr(self.actions.auto_dj, 'playback_device', None)
        playback_device_name = getattr(self.actions.auto_dj, 'playback_device_name', None)

        paused = self.actions.auto_dj.queue_paused()

        return {
            "enabled": True,
            "paused": bool(paused),
            "queued_tracks": queued_tracks,
            "playback_device_id": playback_device_id,
            "playback_device_name": playback_device_name,
        }

    async def pause_queue(self) -> bool:
        if not getattr(self.actions, 'chatdj_enabled', False):
            return False
        if not hasattr(self.actions, 'auto_dj'):
            return False
        return bool(self.actions.auto_dj.pause_queue())

    async def resume_queue(self) -> bool:
        if not getattr(self.actions, 'chatdj_enabled', False):
            return False
        if not hasattr(self.actions, 'auto_dj'):
            return False
        return bool(self.actions.auto_dj.unpause_queue())

    async def get_devices(self) -> list[dict]:
        if not getattr(self.actions, 'chatdj_enabled', False):
            return []
        if not hasattr(self.actions, 'auto_dj'):
            return []
        loop = asyncio.get_running_loop()
        try:
            devices = await asyncio.wait_for(
                loop.run_in_executor(None, self.actions.auto_dj.get_available_devices),
                timeout=5,
            )
        except asyncio.TimeoutError:
            return []
        return devices if isinstance(devices, list) else []

    async def set_device(self, device_id: Any, persist: bool = True) -> bool:
        if not getattr(self.actions, 'chatdj_enabled', False):
            return False
        if not hasattr(self.actions, 'auto_dj'):
            return False
        if not isinstance(device_id, str) or device_id.strip() == '':
            return False

        loop = asyncio.get_running_loop()
        try:
            ok = await asyncio.wait_for(
                loop.run_in_executor(None, self.actions.auto_dj.set_playback_device, device_id, False, False),
                timeout=10,
            )
        except asyncio.TimeoutError:
            return False
        if not ok:
            return False

        if persist:
            await self.update_config_from_ui({"Spotify": {"playback_device_id": device_id}})
        return True

    def get_config_for_ui(self) -> Dict[str, Dict[str, str]]:
        cfg: Dict[str, Dict[str, str]] = {}
        for section in ("Events API", "OpenAI", "Spotify", "Search", "General", "OBS", "Web"):
            if not config.has_section(section):
                continue
            cfg[section] = {}
            for key, val in config.items(section):
                if _is_secret_field(section, key):
                    cfg[section][key] = ""
                else:
                    cfg[section][key] = val
        return cfg

    async def update_config_from_ui(self, payload: Any) -> tuple[bool, Optional[str]]:
        if not isinstance(payload, dict):
            return (False, "Invalid JSON")

        allowed: Dict[str, set[str]] = {
            "Events API": {"url", "max_requests_per_minute"},
            "OpenAI": {"api_key", "model"},
            "Spotify": {"client_id", "client_secret", "redirect_url", "playback_device_id"},
            "Search": {"google_api_key", "google_cx"},
            "General": {"song_cost", "skip_song_cost", "request_overlay_duration", "setup_complete"},
            "OBS": {"enabled", "host", "port", "password"},
            "Web": {"host", "port"},
        }

        updates: Dict[str, Dict[str, str]] = {}
        for section, options in payload.items():
            if section not in allowed:
                continue
            if not isinstance(options, dict):
                continue
            for key, value in options.items():
                if key not in allowed[section]:
                    continue
                if value is None:
                    continue

                value_str = str(value)
                if _is_secret_field(section, key) and value_str.strip() == "":
                    continue

                updates.setdefault(section, {})[key] = value_str

        if not updates:
            return (True, None)

        try:
            _update_ini_file(config_path, updates)
        except Exception as exc:
            return (False, str(exc))

        try:
            config.read(config_path)
        except Exception:
            pass

        try:
            from helpers import config as helpers_config
            from helpers import refresh_spotify_client
            helpers_config.read(config_path)
            refresh_spotify_client()
        except Exception:
            pass

        try:
            self.checks = Checks()
        except Exception:
            pass

        try:
            if getattr(self.actions, 'chatdj_enabled', False):
                from chatdj import SongExtractor
                from helpers import spotify_client

                google_api_key = config.get("Search", "google_api_key", fallback=None) if config.has_section("Search") else None
                google_cx = config.get("Search", "google_cx", fallback=None) if config.has_section("Search") else None

                openai_api_key = config.get("OpenAI", "api_key", fallback="").strip()
                if openai_api_key:
                    self.actions.song_extractor = SongExtractor(
                        openai_api_key,
                        spotify_client=spotify_client,
                        google_api_key=google_api_key,
                        google_cx=google_cx,
                        model=config.get("OpenAI", "model", fallback="gpt-5")
                    )
                self.actions.request_overlay_duration = config.getint("General", "request_overlay_duration", fallback=10)
        except Exception:
            pass

        return (True, None)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    loop = asyncio.get_event_loop()
    loop.set_exception_handler(handle_exception)

    service = SongRequestService()
    await service.start()

    signals = (signal.SIGTERM, signal.SIGINT)
    for s in signals:
        try:
            loop.add_signal_handler(s, lambda s=s: shutdown_event.set())
        except NotImplementedError:
            pass

    await shutdown_event.wait()
    await service.stop()


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
