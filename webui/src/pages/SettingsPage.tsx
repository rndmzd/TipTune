import { useEffect, useState } from 'react';

import { apiJson } from '../api';
import type { Device, QueueState } from '../types';
import { HeaderBar } from '../components/HeaderBar';

type DevicesResp = { ok: true; devices: Device[] };
type QueueResp = { ok: true; queue: QueueState };
type ConfigResp = { ok: true; config: Record<string, Record<string, string>> };

export function SettingsPage() {
  const [currentDeviceText, setCurrentDeviceText] = useState<string>('Loading...');
  const [devices, setDevices] = useState<Device[]>([]);
  const [deviceId, setDeviceId] = useState<string>('');

  const [cfg, setCfg] = useState<Record<string, Record<string, string>>>({});
  const [secrets, setSecrets] = useState({
    eventsUrl: '',
    openaiKey: '',
    spotifySecret: '',
    googleKey: '',
    obsPassword: '',
  });

  const [status, setStatus] = useState<string>('');

  async function refreshCurrentDevice() {
    const data = await apiJson<QueueResp>('/api/queue');
    const st = data.queue ?? {};
    const devName = st.playback_device_name || '';
    const devId = st.playback_device_id || '';
    setCurrentDeviceText(devId ? `Current: ${devName ? `${devName} ` : ''}(${devId})` : 'Current: (none)');
  }

  async function refreshDevices() {
    const data = await apiJson<DevicesResp>('/api/spotify/devices');
    setDevices(data.devices || []);

    try {
      const qst = await apiJson<QueueResp>('/api/queue');
      const cur = (qst.queue || {}).playback_device_id;
      if (cur) setDeviceId(String(cur));
    } catch {
    }
  }

  async function loadConfig() {
    const data = await apiJson<ConfigResp>('/api/config');
    setCfg(data.config || {});
  }

  useEffect(() => {
    Promise.all([
      refreshCurrentDevice().catch((e) => setCurrentDeviceText(`Error: ${e?.message ? e.message : String(e)}`)),
      refreshDevices().catch(() => {}),
      loadConfig().catch((e) => setStatus(`Error loading config: ${e?.message ? e.message : String(e)}`)),
    ]).catch(() => {});
  }, []);

  const v = (section: string, key: string) => ((cfg[section] || {})[key] || '').toString();

  return (
    <>
      <HeaderBar
        title="Settings"
      />

      <div className="row">
        <div className="card">
          <h2>Playback Device</h2>
          <div className="muted">{currentDeviceText}</div>
          <label htmlFor="deviceSelect">Available devices</label>
          <select id="deviceSelect" value={deviceId} onChange={(e) => setDeviceId(e.target.value)}>
            {(devices || []).map((d, idx) => (
              <option key={idx} value={d.id || ''}>
                {(d.name || '(unknown)') + (d.is_active ? ' (active)' : '')}
              </option>
            ))}
          </select>
          <div className="actions">
            <button type="button" onClick={() => refreshDevices().catch(() => {})}>
              Refresh
            </button>
            <button
              type="button"
              onClick={async () => {
                await apiJson('/api/spotify/device', {
                  method: 'POST',
                  headers: { 'Content-Type': 'application/json' },
                  body: JSON.stringify({ device_id: deviceId, persist: true }),
                });
                await refreshCurrentDevice();
                await refreshDevices();
              }}
            >
              Apply + Save
            </button>
          </div>
        </div>
      </div>

      <div className="row" style={{ marginTop: 16 }}>
        <div className="card" style={{ flex: 1, minWidth: 360 }}>
          <h2>Events API</h2>
          <label>URL (secret)</label>
          <input
            type="password"
            placeholder="(leave blank to keep)"
            value={secrets.eventsUrl}
            onChange={(e) => setSecrets((s) => ({ ...s, eventsUrl: e.target.value }))}
          />
          <label>max_requests_per_minute</label>
          <input
            type="text"
            value={v('Events API', 'max_requests_per_minute')}
            onChange={(e) => setCfg((c) => ({ ...c, 'Events API': { ...(c['Events API'] || {}), max_requests_per_minute: e.target.value } }))}
          />
        </div>

        <div className="card" style={{ flex: 1, minWidth: 360 }}>
          <h2>OpenAI</h2>
          <label>API key (secret)</label>
          <input
            type="password"
            placeholder="(leave blank to keep)"
            value={secrets.openaiKey}
            onChange={(e) => setSecrets((s) => ({ ...s, openaiKey: e.target.value }))}
          />
          <label>Model</label>
          <input
            type="text"
            value={v('OpenAI', 'model')}
            onChange={(e) => setCfg((c) => ({ ...c, OpenAI: { ...(c.OpenAI || {}), model: e.target.value } }))}
          />
        </div>
      </div>

      <div className="row" style={{ marginTop: 16 }}>
        <div className="card" style={{ flex: 1, minWidth: 360 }}>
          <h2>Spotify</h2>
          <label>client_id</label>
          <input
            type="text"
            value={v('Spotify', 'client_id')}
            onChange={(e) => setCfg((c) => ({ ...c, Spotify: { ...(c.Spotify || {}), client_id: e.target.value } }))}
          />
          <label>client_secret (secret)</label>
          <input
            type="password"
            placeholder="(leave blank to keep)"
            value={secrets.spotifySecret}
            onChange={(e) => setSecrets((s) => ({ ...s, spotifySecret: e.target.value }))}
          />
          <label>redirect_url</label>
          <input
            type="text"
            value={v('Spotify', 'redirect_url')}
            onChange={(e) => setCfg((c) => ({ ...c, Spotify: { ...(c.Spotify || {}), redirect_url: e.target.value } }))}
          />
        </div>

        <div className="card" style={{ flex: 1, minWidth: 360 }}>
          <h2>OBS</h2>
          <label>enabled</label>
          <select
            value={(v('OBS', 'enabled') || 'false').toLowerCase()}
            onChange={(e) => setCfg((c) => ({ ...c, OBS: { ...(c.OBS || {}), enabled: e.target.value } }))}
          >
            <option value="true">true</option>
            <option value="false">false</option>
          </select>
          <label>host</label>
          <input
            type="text"
            value={v('OBS', 'host')}
            onChange={(e) => setCfg((c) => ({ ...c, OBS: { ...(c.OBS || {}), host: e.target.value } }))}
          />
          <label>port</label>
          <input
            type="text"
            value={v('OBS', 'port')}
            onChange={(e) => setCfg((c) => ({ ...c, OBS: { ...(c.OBS || {}), port: e.target.value } }))}
          />
          <label>password (secret)</label>
          <input
            type="password"
            placeholder="(leave blank to keep)"
            value={secrets.obsPassword}
            onChange={(e) => setSecrets((s) => ({ ...s, obsPassword: e.target.value }))}
          />
        </div>
      </div>

      <div className="row" style={{ marginTop: 16 }}>
        <div className="card" style={{ flex: 1, minWidth: 360 }}>
          <h2>Search</h2>
          <label>google_api_key (secret)</label>
          <input
            type="password"
            placeholder="(leave blank to keep)"
            value={secrets.googleKey}
            onChange={(e) => setSecrets((s) => ({ ...s, googleKey: e.target.value }))}
          />
          <label>google_cx</label>
          <input
            type="text"
            value={v('Search', 'google_cx')}
            onChange={(e) => setCfg((c) => ({ ...c, Search: { ...(c.Search || {}), google_cx: e.target.value } }))}
          />
        </div>

        <div className="card" style={{ flex: 1, minWidth: 360 }}>
          <h2>General</h2>
          <label>song_cost</label>
          <input
            type="text"
            value={v('General', 'song_cost')}
            onChange={(e) => setCfg((c) => ({ ...c, General: { ...(c.General || {}), song_cost: e.target.value } }))}
          />
          <label>skip_song_cost</label>
          <input
            type="text"
            value={v('General', 'skip_song_cost')}
            onChange={(e) => setCfg((c) => ({ ...c, General: { ...(c.General || {}), skip_song_cost: e.target.value } }))}
          />
          <label>request_overlay_duration</label>
          <input
            type="text"
            value={v('General', 'request_overlay_duration')}
            onChange={(e) => setCfg((c) => ({ ...c, General: { ...(c.General || {}), request_overlay_duration: e.target.value } }))}
          />
        </div>
      </div>

      <div className="card" style={{ marginTop: 16 }}>
        <div className="actions">
          <button
            type="button"
            onClick={async () => {
              setStatus('Saving...');

              const payload: Record<string, Record<string, string>> = {
                'Events API': {
                  url: secrets.eventsUrl,
                  max_requests_per_minute: v('Events API', 'max_requests_per_minute'),
                },
                OpenAI: {
                  api_key: secrets.openaiKey,
                  model: v('OpenAI', 'model'),
                },
                Spotify: {
                  client_id: v('Spotify', 'client_id'),
                  client_secret: secrets.spotifySecret,
                  redirect_url: v('Spotify', 'redirect_url'),
                },
                Search: {
                  google_api_key: secrets.googleKey,
                  google_cx: v('Search', 'google_cx'),
                },
                General: {
                  song_cost: v('General', 'song_cost'),
                  skip_song_cost: v('General', 'skip_song_cost'),
                  request_overlay_duration: v('General', 'request_overlay_duration'),
                },
                OBS: {
                  enabled: v('OBS', 'enabled'),
                  host: v('OBS', 'host'),
                  port: v('OBS', 'port'),
                  password: secrets.obsPassword,
                },
              };

              try {
                await apiJson('/api/config', {
                  method: 'POST',
                  headers: { 'Content-Type': 'application/json' },
                  body: JSON.stringify(payload),
                });

                setStatus('Saved.');
                setSecrets({ eventsUrl: '', openaiKey: '', spotifySecret: '', googleKey: '', obsPassword: '' });
                await loadConfig();
              } catch (e: any) {
                setStatus(`Error: ${e?.message ? e.message : String(e)}`);
              }
            }}
          >
            Save Settings
          </button>
          <span className="muted">{status}</span>
        </div>
      </div>
    </>
  );
}
