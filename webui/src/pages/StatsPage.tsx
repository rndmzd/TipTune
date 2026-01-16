import { useEffect, useMemo, useState } from 'react';

import { apiJson } from '../api';
import { HeaderBar } from '../components/HeaderBar';

type HistoryRecentResp = { ok: true; history: any[] };

type SortDir = 'asc' | 'desc';

type SortKeyUsers =
  | 'user'
  | 'request_tips'
  | 'songs_added'
  | 'songs_failed'
  | 'paid_slots'
  | 'tokens'
  | 'last_ts';

type SortKeySongs =
  | 'song'
  | 'requests'
  | 'added'
  | 'failed'
  | 'unique_users'
  | 'last_ts';

function toMs(ts: any): number | null {
  try {
    if (typeof ts === 'number') {
      if (!Number.isFinite(ts)) return null;
      if (ts > 5_000_000_000) return Math.round(ts);
      return Math.round(ts * 1000);
    }
    if (typeof ts === 'string') {
      const d = new Date(ts);
      const ms = d.getTime();
      if (Number.isNaN(ms)) return null;
      return ms;
    }
    return null;
  } catch {
    return null;
  }
}

function toLocalTimeLabelMs(ms: number | null): string {
  if (ms == null) return '';
  try {
    const d = new Date(ms);
    if (Number.isNaN(d.getTime())) return '';
    return d.toLocaleString();
  } catch {
    return '';
  }
}

function str(v: any): string {
  return typeof v === 'string' ? v : v == null ? '' : String(v);
}

function normKey(s: string): string {
  return (s || '').trim().toLowerCase();
}

function songIdentity(item: any) {
  const resolvedUri = typeof item?.resolved_uri === 'string' ? item.resolved_uri.trim() : '';
  const spotifyTrack = item?.spotify_track && typeof item.spotify_track === 'object' ? item.spotify_track : null;

  const spotifyName = spotifyTrack && typeof spotifyTrack.name === 'string' ? spotifyTrack.name.trim() : '';
  const spotifyArtistsRaw = spotifyTrack && Array.isArray(spotifyTrack.artists) ? spotifyTrack.artists : null;
  const spotifyArtists = spotifyArtistsRaw ? spotifyArtistsRaw.filter((v: any) => typeof v === 'string' && v.trim() !== '').join(', ') : '';
  const spotifyUrl = spotifyTrack && typeof spotifyTrack.spotify_url === 'string' ? spotifyTrack.spotify_url.trim() : '';

  const songDetails = typeof item?.song_details === 'string' ? item.song_details.trim() : '';
  const artist = typeof item?.artist === 'string' ? item.artist.trim() : '';
  const song = typeof item?.song === 'string' ? item.song.trim() : '';

  const title = spotifyName || song || (songDetails ? songDetails.split(' - ').slice(1).join(' - ') : '') || '';
  const artistName = spotifyArtists || artist || (songDetails ? songDetails.split(' - ')[0] : '') || '';
  const display = [artistName, title].filter((v) => v && v.trim() !== '').join(' — ') || 'Unknown song';

  const key =
    resolvedUri ||
    (spotifyName || spotifyArtists ? `spotify:${spotifyArtists}:${spotifyName}` : '') ||
    (songDetails ? `text:${songDetails}` : '') ||
    (artist || song ? `text:${artist} - ${song}` : '') ||
    (typeof item?.tip_message === 'string' ? `text:${item.tip_message.trim()}` : '') ||
    'unknown';

  return { key, title, artistName, display, resolvedUri, spotifyUrl };
}

function tipEventKey(item: any): string {
  const u = str(item?.username).trim() || 'Unknown';
  const tipAmount = typeof item?.tip_amount === 'number' && Number.isFinite(item.tip_amount) ? String(item.tip_amount) : '';
  const msg = str(item?.tip_message).trim();
  const ms = toMs(item?.tip_ts) ?? toMs(item?.ts);
  const tsKey = ms == null ? '' : String(ms);
  return `${u}|${tipAmount}|${msg}|${tsKey}`;
}

function cmp(a: any, b: any): number {
  if (a === b) return 0;
  if (a == null) return -1;
  if (b == null) return 1;
  if (typeof a === 'number' && typeof b === 'number') return a < b ? -1 : 1;
  return String(a).localeCompare(String(b));
}

function toggleSort<T extends string>(curKey: T, curDir: SortDir, nextKey: T): { key: T; dir: SortDir } {
  if (curKey !== nextKey) return { key: nextKey, dir: 'desc' };
  return { key: nextKey, dir: curDir === 'desc' ? 'asc' : 'desc' };
}

export function StatsPage() {
  const [status, setStatus] = useState<'loading' | 'ok' | 'error'>('loading');
  const [err, setErr] = useState<string>('');
  const [items, setItems] = useState<any[]>([]);
  const [limit, setLimit] = useState<number>(500);

  const [timeWindow, setTimeWindow] = useState<'24h' | '7d' | '30d' | 'all'>('30d');
  const [statusFilter, setStatusFilter] = useState<'all' | 'added' | 'failed'>('all');
  const [q, setQ] = useState<string>('');

  const [selectedUser, setSelectedUser] = useState<string>('');
  const [selectedSongKey, setSelectedSongKey] = useState<string>('');

  const [sortUsers, setSortUsers] = useState<{ key: SortKeyUsers; dir: SortDir }>({ key: 'songs_added', dir: 'desc' });
  const [sortSongs, setSortSongs] = useState<{ key: SortKeySongs; dir: SortDir }>({ key: 'requests', dir: 'desc' });

  async function refresh() {
    setStatus((prev) => (prev === 'ok' ? 'loading' : prev));
    setErr('');
    try {
      const j = await apiJson<HistoryRecentResp>(`/api/history/recent?limit=${encodeURIComponent(String(limit))}`);
      const hist = Array.isArray(j?.history) ? j.history : [];
      setItems(hist);
      setStatus('ok');
    } catch (e: any) {
      setErr(e?.message ? String(e.message) : String(e));
      setStatus('error');
    }
  }

  useEffect(() => {
    refresh().catch(() => {});
  }, []);

  const filteredItems = useMemo(() => {
    const list = Array.isArray(items) ? items : [];

    const now = Date.now();
    const windowMs =
      timeWindow === '24h' ? 24 * 60 * 60 * 1000 : timeWindow === '7d' ? 7 * 24 * 60 * 60 * 1000 : timeWindow === '30d' ? 30 * 24 * 60 * 60 * 1000 : null;

    const qn = normKey(q);

    return list.filter((it) => {
      const st = str(it?.status).trim();
      if (statusFilter !== 'all' && st !== statusFilter) return false;

      const ms = toMs(it?.ts);
      if (windowMs != null && ms != null) {
        if (ms < now - windowMs) return false;
      }

      const u = str(it?.username).trim();
      if (selectedUser && u !== selectedUser) return false;

      const s = songIdentity(it);
      if (selectedSongKey && s.key !== selectedSongKey) return false;

      if (qn) {
        const hay = `${u} ${s.display} ${str(it?.tip_message)} ${str(it?.song_details)}`;
        if (!normKey(hay).includes(qn)) return false;
      }

      return true;
    });
  }, [items, q, selectedSongKey, selectedUser, statusFilter, timeWindow]);

  const summary = useMemo(() => {
    const list = filteredItems;

    const uniqueUsers = new Set<string>();
    let added = 0;
    let failed = 0;

    const tipEvents = new Map<
      string,
      {
        tip_amount: number;
        request_count: number;
      }
    >();

    for (const it of list) {
      const u = str(it?.username).trim() || 'Unknown';
      uniqueUsers.add(u);

      const st = str(it?.status).trim();
      if (st === 'added') added++;
      if (st === 'failed') failed++;

      const key = tipEventKey(it);
      if (!tipEvents.has(key)) {
        const tipAmount = typeof it?.tip_amount === 'number' && Number.isFinite(it.tip_amount) ? it.tip_amount : 0;
        const rc = typeof it?.request_count === 'number' && Number.isFinite(it.request_count) ? it.request_count : 1;
        tipEvents.set(key, { tip_amount: tipAmount, request_count: Math.max(0, Math.floor(rc)) });
      }
    }

    let totalTokens = 0;
    let paidSlots = 0;
    for (const v of tipEvents.values()) {
      totalTokens += v.tip_amount;
      paidSlots += v.request_count;
    }

    const successRate = added + failed > 0 ? Math.round((added / (added + failed)) * 1000) / 10 : 0;

    return {
      totalItems: list.length,
      totalTips: tipEvents.size,
      totalUsers: uniqueUsers.size,
      added,
      failed,
      totalTokens,
      paidSlots,
      successRate,
    };
  }, [filteredItems]);

  const users = useMemo(() => {
    const rows = new Map<
      string,
      {
        user: string;
        requestTips: number;
        songsAdded: number;
        songsFailed: number;
        tokens: number;
        paidSlots: number;
        lastTs: number;
      }
    >();

    const tipEventsByUser = new Map<string, Set<string>>();
    const tipMeta = new Map<string, { tip_amount: number; request_count: number; user: string; ts: number }>();

    for (const it of filteredItems) {
      const user = str(it?.username).trim() || 'Unknown';
      const ms = toMs(it?.ts) ?? 0;
      const key = tipEventKey(it);

      if (!tipMeta.has(key)) {
        const tipAmount = typeof it?.tip_amount === 'number' && Number.isFinite(it.tip_amount) ? it.tip_amount : 0;
        const rc = typeof it?.request_count === 'number' && Number.isFinite(it.request_count) ? it.request_count : 1;
        tipMeta.set(key, { tip_amount: tipAmount, request_count: Math.max(0, Math.floor(rc)), user, ts: ms });
      }

      if (!tipEventsByUser.has(user)) tipEventsByUser.set(user, new Set());
      tipEventsByUser.get(user)!.add(key);

      if (!rows.has(user)) {
        rows.set(user, {
          user,
          requestTips: 0,
          songsAdded: 0,
          songsFailed: 0,
          tokens: 0,
          paidSlots: 0,
          lastTs: ms,
        });
      }

      const r = rows.get(user)!;
      r.lastTs = Math.max(r.lastTs, ms);

      const st = str(it?.status).trim();
      if (st === 'added') r.songsAdded++;
      if (st === 'failed') r.songsFailed++;
    }

    for (const [user, keys] of tipEventsByUser.entries()) {
      const r = rows.get(user);
      if (!r) continue;
      r.requestTips = keys.size;
      for (const k of keys) {
        const meta = tipMeta.get(k);
        if (!meta) continue;
        r.tokens += meta.tip_amount;
        r.paidSlots += meta.request_count;
      }
    }

    const out = Array.from(rows.values());
    out.sort((a, b) => {
      const dir = sortUsers.dir === 'desc' ? -1 : 1;
      const ka =
        sortUsers.key === 'user'
          ? a.user
          : sortUsers.key === 'request_tips'
            ? a.requestTips
            : sortUsers.key === 'songs_added'
              ? a.songsAdded
              : sortUsers.key === 'songs_failed'
                ? a.songsFailed
                : sortUsers.key === 'paid_slots'
                  ? a.paidSlots
                  : sortUsers.key === 'tokens'
                    ? a.tokens
                    : a.lastTs;
      const kb =
        sortUsers.key === 'user'
          ? b.user
          : sortUsers.key === 'request_tips'
            ? b.requestTips
            : sortUsers.key === 'songs_added'
              ? b.songsAdded
              : sortUsers.key === 'songs_failed'
                ? b.songsFailed
                : sortUsers.key === 'paid_slots'
                  ? b.paidSlots
                  : sortUsers.key === 'tokens'
                    ? b.tokens
                    : b.lastTs;
      return cmp(ka, kb) * dir;
    });

    return out;
  }, [filteredItems, sortUsers.dir, sortUsers.key]);

  const songs = useMemo(() => {
    const rows = new Map<
      string,
      {
        key: string;
        display: string;
        title: string;
        artistName: string;
        spotifyUrl: string;
        requests: number;
        added: number;
        failed: number;
        users: Set<string>;
        lastTs: number;
      }
    >();

    for (const it of filteredItems) {
      const u = str(it?.username).trim() || 'Unknown';
      const s = songIdentity(it);
      const ms = toMs(it?.ts) ?? 0;

      if (!rows.has(s.key)) {
        rows.set(s.key, {
          key: s.key,
          display: s.display,
          title: s.title,
          artistName: s.artistName,
          spotifyUrl: s.spotifyUrl,
          requests: 0,
          added: 0,
          failed: 0,
          users: new Set<string>(),
          lastTs: ms,
        });
      }

      const r = rows.get(s.key)!;
      r.display = s.display || r.display;
      r.title = s.title || r.title;
      r.artistName = s.artistName || r.artistName;
      r.spotifyUrl = s.spotifyUrl || r.spotifyUrl;

      r.requests++;
      r.users.add(u);
      r.lastTs = Math.max(r.lastTs, ms);

      const st = str(it?.status).trim();
      if (st === 'added') r.added++;
      if (st === 'failed') r.failed++;
    }

    const out = Array.from(rows.values()).map((r) => ({
      key: r.key,
      display: r.display,
      title: r.title,
      artistName: r.artistName,
      spotifyUrl: r.spotifyUrl,
      requests: r.requests,
      added: r.added,
      failed: r.failed,
      uniqueUsers: r.users.size,
      lastTs: r.lastTs,
    }));

    out.sort((a, b) => {
      const dir = sortSongs.dir === 'desc' ? -1 : 1;
      const ka =
        sortSongs.key === 'song'
          ? a.display
          : sortSongs.key === 'requests'
            ? a.requests
            : sortSongs.key === 'added'
              ? a.added
              : sortSongs.key === 'failed'
                ? a.failed
                : sortSongs.key === 'unique_users'
                  ? a.uniqueUsers
                  : a.lastTs;
      const kb =
        sortSongs.key === 'song'
          ? b.display
          : sortSongs.key === 'requests'
            ? b.requests
            : sortSongs.key === 'added'
              ? b.added
              : sortSongs.key === 'failed'
                ? b.failed
                : sortSongs.key === 'unique_users'
                  ? b.uniqueUsers
                  : b.lastTs;
      return cmp(ka, kb) * dir;
    });

    return out;
  }, [filteredItems, sortSongs.dir, sortSongs.key]);

  const maxSongRequests = useMemo(() => {
    let m = 0;
    for (const s of songs) m = Math.max(m, s.requests);
    return m;
  }, [songs]);

  const maxUserAdded = useMemo(() => {
    let m = 0;
    for (const u of users) m = Math.max(m, u.songsAdded);
    return m;
  }, [users]);

  return (
    <div className="page-events">
      <HeaderBar title="Stats" />

      <div className="row" style={{ marginTop: 12 }}>
        <button type="button" onClick={() => refresh()}>
          Refresh
        </button>
        <span className="muted">Aggregated from saved request history (up to 500 items).</span>
      </div>

      <div className="row" style={{ marginTop: 12, alignItems: 'flex-end' }}>
        <div style={{ minWidth: 160 }}>
          <label className="muted">Time window</label>
          <select value={timeWindow} onChange={(e) => setTimeWindow(e.target.value as any)}>
            <option value="24h">Last 24h</option>
            <option value="7d">Last 7 days</option>
            <option value="30d">Last 30 days</option>
            <option value="all">All</option>
          </select>
        </div>

        <div style={{ minWidth: 160 }}>
          <label className="muted">Status</label>
          <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value as any)}>
            <option value="all">All</option>
            <option value="added">Added</option>
            <option value="failed">Failed</option>
          </select>
        </div>

        <div style={{ minWidth: 220, flex: 1 }}>
          <label className="muted">Search</label>
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="User, song, artist, tip message…" />
        </div>

        <div style={{ minWidth: 140 }}>
          <label className="muted">History limit</label>
          <input
            type="number"
            min={1}
            max={500}
            value={String(limit)}
            onChange={(e) => setLimit(Number.parseInt(e.target.value || '0', 10) || 500)}
          />
        </div>

        {selectedUser || selectedSongKey ? (
          <button
            type="button"
            onClick={() => {
              setSelectedUser('');
              setSelectedSongKey('');
            }}
          >
            Clear selection
          </button>
        ) : null}

        {status === 'loading' ? <span className="muted">Loading…</span> : null}
        {status === 'error' && err ? <span className="muted">{err}</span> : null}
      </div>

      <div className="row" style={{ marginTop: 12 }}>
        <div className="card">
          <h2>Summary</h2>
          <div className="row">
            <span className="pill pillNeutral">{`Items: ${summary.totalItems}`}</span>
            <span className="pill pillNeutral">{`Request tips: ${summary.totalTips}`}</span>
            <span className="pill pillNeutral">{`Users: ${summary.totalUsers}`}</span>
            <span className="pill pillInfo">{`Added: ${summary.added}`}</span>
            <span className="pill pillWarn">{`Failed: ${summary.failed}`}</span>
            <span className="pill pillNeutral">{`Tokens: ${summary.totalTokens}`}</span>
            <span className="pill pillNeutral">{`Paid slots: ${summary.paidSlots}`}</span>
            <span className="pill pillNeutral">{`Success: ${summary.successRate}%`}</span>
          </div>
          {selectedUser ? <div className="muted" style={{ marginTop: 10 }}>{`Filtered to user: ${selectedUser}`}</div> : null}
          {selectedSongKey ? <div className="muted" style={{ marginTop: 6 }}>Filtered to selected song.</div> : null}
        </div>
      </div>

      <div className="row" style={{ marginTop: 12 }}>
        <div className="card" style={{ flex: 1, minWidth: 420 }}>
          <h2>Users</h2>
          <div className="tableWrap" style={{ maxHeight: '60vh' }}>
            <table className="dataTable">
              <thead>
                <tr>
                  <th
                    className="sortable"
                    onClick={() => setSortUsers((s) => toggleSort(s.key, s.dir, 'user'))}
                  >
                    User
                  </th>
                  <th
                    className="right sortable"
                    onClick={() => setSortUsers((s) => toggleSort(s.key, s.dir, 'request_tips'))}
                  >
                    Tips
                  </th>
                  <th
                    className="right sortable"
                    onClick={() => setSortUsers((s) => toggleSort(s.key, s.dir, 'songs_added'))}
                  >
                    Added
                  </th>
                  <th
                    className="right sortable"
                    onClick={() => setSortUsers((s) => toggleSort(s.key, s.dir, 'songs_failed'))}
                  >
                    Failed
                  </th>
                  <th
                    className="right sortable"
                    onClick={() => setSortUsers((s) => toggleSort(s.key, s.dir, 'paid_slots'))}
                  >
                    Slots
                  </th>
                  <th
                    className="right sortable"
                    onClick={() => setSortUsers((s) => toggleSort(s.key, s.dir, 'tokens'))}
                  >
                    Tokens
                  </th>
                  <th
                    className="sortable"
                    onClick={() => setSortUsers((s) => toggleSort(s.key, s.dir, 'last_ts'))}
                  >
                    Last
                  </th>
                </tr>
              </thead>
              <tbody>
                {users.map((u) => {
                  const active = selectedUser && selectedUser === u.user;
                  const barW = maxUserAdded > 0 ? Math.round((u.songsAdded / maxUserAdded) * 140) : 0;
                  return (
                    <tr
                      key={u.user}
                      className={['clickable', active ? 'active' : ''].filter(Boolean).join(' ')}
                      onClick={() => {
                        setSelectedSongKey('');
                        setSelectedUser((prev) => (prev === u.user ? '' : u.user));
                      }}
                    >
                      <td>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                          <span className={active ? 'pill pillInfo' : 'pill pillNeutral'}>{u.user}</span>
                          <div className="progressTrack" style={{ width: 140 }}>
                            <div className="progressFill" style={{ width: barW }} />
                          </div>
                        </div>
                      </td>
                      <td className="right">{u.requestTips}</td>
                      <td className="right">{u.songsAdded}</td>
                      <td className="right">{u.songsFailed}</td>
                      <td className="right">{u.paidSlots}</td>
                      <td className="right">{u.tokens}</td>
                      <td>{toLocalTimeLabelMs(u.lastTs)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>

        <div className="card" style={{ flex: 1, minWidth: 520 }}>
          <h2>Songs</h2>
          <div className="tableWrap" style={{ maxHeight: '60vh' }}>
            <table className="dataTable">
              <thead>
                <tr>
                  <th
                    className="sortable"
                    onClick={() => setSortSongs((s) => toggleSort(s.key, s.dir, 'song'))}
                  >
                    Song
                  </th>
                  <th
                    className="right sortable"
                    onClick={() => setSortSongs((s) => toggleSort(s.key, s.dir, 'requests'))}
                  >
                    Req
                  </th>
                  <th
                    className="right sortable"
                    onClick={() => setSortSongs((s) => toggleSort(s.key, s.dir, 'added'))}
                  >
                    Added
                  </th>
                  <th
                    className="right sortable"
                    onClick={() => setSortSongs((s) => toggleSort(s.key, s.dir, 'failed'))}
                  >
                    Failed
                  </th>
                  <th
                    className="right sortable"
                    onClick={() => setSortSongs((s) => toggleSort(s.key, s.dir, 'unique_users'))}
                  >
                    Users
                  </th>
                  <th
                    className="sortable"
                    onClick={() => setSortSongs((s) => toggleSort(s.key, s.dir, 'last_ts'))}
                  >
                    Last
                  </th>
                </tr>
              </thead>
              <tbody>
                {songs.map((s) => {
                  const active = selectedSongKey && selectedSongKey === s.key;
                  const barW = maxSongRequests > 0 ? Math.round((s.requests / maxSongRequests) * 160) : 0;
                  return (
                    <tr
                      key={s.key}
                      className={['clickable', active ? 'active' : ''].filter(Boolean).join(' ')}
                      onClick={() => {
                        setSelectedUser('');
                        setSelectedSongKey((prev) => (prev === s.key ? '' : s.key));
                      }}
                    >
                      <td>
                        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                          <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                            <span className={active ? 'pill pillInfo' : 'pill pillNeutral'} style={{ maxWidth: 520, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                              {s.display}
                            </span>
                            {s.spotifyUrl ? (
                              <a className="pill pillNeutral" href={s.spotifyUrl} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()}>
                                Open
                              </a>
                            ) : null}
                          </div>
                          <div className="progressTrack" style={{ width: 160 }}>
                            <div className="progressFill" style={{ width: barW }} />
                          </div>
                        </div>
                      </td>
                      <td className="right">{s.requests}</td>
                      <td className="right">{s.added}</td>
                      <td className="right">{s.failed}</td>
                      <td className="right">{s.uniqueUsers}</td>
                      <td>{toLocalTimeLabelMs(s.lastTs)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  );
}
