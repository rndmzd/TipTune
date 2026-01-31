import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

import { apiJson, sseUrl } from '../api';
import type { QueueItem, QueueState } from '../types';

type QueueResp = { ok: true; queue: QueueState };

export type YoutubeDebugInfo = {
  event: string;
  src: string;
  currentSrc: string;
  readyState: number;
  networkState: number;
  errorCode: number | null;
  paused: boolean;
  duration: number | null;
  currentTime: number | null;
};

type PlaybackContextValue = {
  nowPlaying: QueueItem | null;
  queueState: QueueState | null;
  paused: boolean;
  playbackPosMs: number | null;
  durationMs: number | null;
  posClampedMs: number | null;
  pct: number | null;
  isYouTube: boolean;
  youtubeStreamUrl: string;
  youtubeDebugInfo: YoutubeDebugInfo | null;
  refresh: () => Promise<void>;
  seekTo: (ms: number) => void;
  pausePlayback: () => Promise<void>;
  resumePlayback: () => Promise<void>;
  setPlayerDock: (el: HTMLElement | null) => void;
};

const PlaybackContext = createContext<PlaybackContextValue | null>(null);

export function usePlayback(): PlaybackContextValue {
  const ctx = useContext(PlaybackContext);
  if (!ctx) {
    throw new Error('usePlayback must be used within PlaybackProvider');
  }
  return ctx;
}

function fmtTime(ms: number | null): string {
  const n = Number(ms);
  if (!Number.isFinite(n) || n < 0) return '0:00';
  const s = Math.floor(n / 1000);
  const m = Math.floor(s / 60);
  const r = s % 60;
  return `${m}:${String(r).padStart(2, '0')}`;
}

function isYouTubeLink(value: string): boolean {
  const s = String(value || '').trim();
  if (!s) return false;
  try {
    const u = new URL(s);
    const host = String(u.hostname || '').toLowerCase();
    if (host === 'youtu.be' || host === 'youtube.com' || host.endsWith('.youtube.com')) {
      return true;
    }
  } catch {
    const lower = s.toLowerCase();
    return /(^|\W)(youtube\.com|youtu\.be)(\/|$|\?)/i.test(lower);
  }
  return false;
}

export function PlaybackProvider(props: { children: React.ReactNode }) {
  const [queueState, setQueueState] = useState<QueueState | null>(null);
  const [nowPlaying, setNowPlaying] = useState<QueueItem | null>(null);
  const [paused, setPaused] = useState<boolean>(false);

  const [dockEl, setDockEl] = useState<HTMLElement | null>(null);
  const playerHostRef = useRef<HTMLDivElement | null>(null);
  if (playerHostRef.current == null && typeof document !== 'undefined') {
    const host = document.createElement('div');
    host.className = 'youtubePlayerHost';
    playerHostRef.current = host;
  }

  const [playbackPosMs, setPlaybackPosMs] = useState<number | null>(null);
  const playbackTickRef = useRef<number | null>(null);
  const playbackTickLastRef = useRef<number>(0);
  const playbackIsPlayingRef = useRef<boolean>(false);

  const youtubeAudioRef = useRef<HTMLAudioElement | null>(null);
  const youtubePlaybackStartedRef = useRef<boolean>(false);
  const [youtubeDebugInfo, setYoutubeDebugInfo] = useState<YoutubeDebugInfo | null>(null);
  const [youtubeTimeMs, setYoutubeTimeMs] = useState<number | null>(null);
  const [youtubeDurationMs, setYoutubeDurationMs] = useState<number | null>(null);

  async function refresh() {
    try {
      const data = await apiJson<QueueResp>('/api/queue');
      const st = data.queue ?? {};
      setQueueState(st);
      setPaused(!!st.paused);

      const np =
        st.now_playing_item && typeof st.now_playing_item === 'object'
          ? (st.now_playing_item as QueueItem)
          : st.now_playing_track
            ? (st.now_playing_track as any as QueueItem)
            : null;
      setNowPlaying(np);
    } catch {
    }
  }

  useEffect(() => {
    refresh().catch(() => {});
    const t = window.setInterval(() => {
      refresh().catch(() => {});
    }, 2000);
    return () => {
      window.clearInterval(t);
    };
  }, []);

  useEffect(() => {
    const host = playerHostRef.current;
    if (!host) return;
    if (!host.parentElement) {
      document.body.appendChild(host);
    }
    return () => {
      host.remove();
    };
  }, []);

  useEffect(() => {
    const host = playerHostRef.current;
    if (!host) return;
    if (dockEl) {
      dockEl.appendChild(host);
      return;
    }
    if (host.parentElement !== document.body) {
      document.body.appendChild(host);
    }
  }, [dockEl]);

  useEffect(() => {
    const serverPos = typeof queueState?.playback_progress_ms === 'number' ? queueState!.playback_progress_ms! : null;
    if (serverPos === null) {
      setPlaybackPosMs(null);
      return;
    }

    setPlaybackPosMs(serverPos);
  }, [queueState?.playback_progress_ms, queueState?.playback_track_uri]);

  useEffect(() => {
    playbackIsPlayingRef.current = !!queueState?.playback_is_playing;

    if (playbackTickRef.current != null) {
      window.clearInterval(playbackTickRef.current);
      playbackTickRef.current = null;
    }

    if (!queueState?.playback_is_playing) return;
    if (typeof queueState?.playback_progress_ms !== 'number') return;

    playbackTickLastRef.current = Date.now();
    playbackTickRef.current = window.setInterval(() => {
      if (!playbackIsPlayingRef.current) return;
      const now = Date.now();
      const delta = now - playbackTickLastRef.current;
      if (delta <= 0) return;
      playbackTickLastRef.current = now;
      setPlaybackPosMs((prev) => {
        if (typeof prev !== 'number') return prev;
        return prev + delta;
      });
    }, 250);

    return () => {
      if (playbackTickRef.current != null) {
        window.clearInterval(playbackTickRef.current);
        playbackTickRef.current = null;
      }
    };
  }, [queueState?.playback_is_playing, queueState?.playback_track_uri, queueState?.playback_progress_ms]);

  const source = String(queueState?.source || 'spotify');
  const inferredYoutube = isYouTubeLink(typeof nowPlaying?.uri === 'string' ? nowPlaying.uri : '');
  const nowSource = inferredYoutube ? 'youtube' : String(nowPlaying?.source || source);
  const isYouTube = nowSource === 'youtube';
  const youtubeStreamUrl =
    isYouTube && typeof nowPlaying?.uri === 'string' && nowPlaying.uri.trim() !== ''
      ? sseUrl(`/api/youtube/stream?url=${encodeURIComponent(nowPlaying.uri)}`)
      : '';

  const displayDurationMs =
    typeof nowPlaying?.duration_ms === 'number'
      ? nowPlaying.duration_ms
      : isYouTube
        ? youtubeDurationMs
        : null;

  const displayPosMs =
    typeof playbackPosMs === 'number'
      ? playbackPosMs
      : isYouTube
        ? youtubeTimeMs
        : null;

  const posClampedMs =
    displayPosMs != null && displayDurationMs != null
      ? Math.max(0, Math.min(displayPosMs, displayDurationMs))
      : displayPosMs;
  const pct =
    displayDurationMs && posClampedMs != null && displayDurationMs > 0
      ? Math.max(0, Math.min(1, posClampedMs / displayDurationMs))
      : null;

  useEffect(() => {
    youtubePlaybackStartedRef.current = false;
  }, [isYouTube, nowPlaying?.uri]);

  useEffect(() => {
    if (!isYouTube) {
      setYoutubeDebugInfo(null);
      setYoutubeTimeMs(null);
      setYoutubeDurationMs(null);
    }
  }, [isYouTube]);

  function updateYoutubeDebug(event: string) {
    const a = youtubeAudioRef.current;
    if (!a) {
      setYoutubeDebugInfo({
        event,
        src: youtubeStreamUrl,
        currentSrc: '',
        readyState: -1,
        networkState: -1,
        errorCode: null,
        paused: true,
        duration: null,
        currentTime: null,
      });
      return;
    }

    const currentTime = Number.isFinite(a.currentTime) ? a.currentTime : null;
    const duration = Number.isFinite(a.duration) ? a.duration : null;

    setYoutubeDebugInfo({
      event,
      src: youtubeStreamUrl,
      currentSrc: a.currentSrc || a.src || '',
      readyState: a.readyState,
      networkState: a.networkState,
      errorCode: a.error ? a.error.code : null,
      paused: a.paused,
      duration,
      currentTime,
    });

    setYoutubeTimeMs(currentTime != null ? Math.round(currentTime * 1000) : null);
    setYoutubeDurationMs(duration != null ? Math.round(duration * 1000) : null);
  }

  function seekTo(ms: number) {
    const a = youtubeAudioRef.current;
    if (!a) return;
    const next = Number(ms);
    if (!Number.isFinite(next) || next < 0) return;
    try {
      a.currentTime = next / 1000;
      updateYoutubeDebug('seek');
    } catch {
    }
  }

  const pausePlayback = useCallback(async () => {
    try {
      await apiJson('/api/queue/pause', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: '{}',
      });
    } catch {
    }
    const a = youtubeAudioRef.current;
    if (a) {
      try {
        a.pause();
      } catch {
      }
    }
    refresh().catch(() => {});
  }, [refresh]);

  const resumePlayback = useCallback(async () => {
    try {
      await apiJson('/api/queue/resume', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: '{}',
      });
    } catch {
    }
    const a = youtubeAudioRef.current;
    if (a) {
      try {
        const p = a.play();
        if (p && typeof (p as any).catch === 'function') {
          (p as any).catch(() => {});
        }
      } catch {
      }
    }
    refresh().catch(() => {});
  }, [refresh]);

  const setPlayerDock = useCallback((el: HTMLElement | null) => {
    setDockEl(el);
  }, []);

  async function nextTrack() {
    try {
      await apiJson('/api/queue/next', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: '{}',
      });
    } catch {
    }
  }

  useEffect(() => {
    if (!isYouTube) return;
    const a = youtubeAudioRef.current;
    if (!a) return;

    if (paused) {
      try {
        a.pause();
      } catch {
      }
      updateYoutubeDebug('pause');
      return;
    }

    if (!youtubeStreamUrl) return;
    updateYoutubeDebug('effect');
    try {
      a.load();
      updateYoutubeDebug('load');
    } catch {
    }

    const t = window.setTimeout(() => {
      try {
        updateYoutubeDebug('play-attempt');
        const p = a.play();
        if (p && typeof (p as any).catch === 'function') {
          (p as any).catch(() => {});
        }
      } catch {
      }
    }, 50);

    return () => {
      window.clearTimeout(t);
    };
  }, [isYouTube, paused, youtubeStreamUrl, nowPlaying?.uri]);

  const ctxValue = useMemo<PlaybackContextValue>(
    () => ({
      nowPlaying,
      queueState,
      paused,
      playbackPosMs: displayPosMs,
      durationMs: displayDurationMs,
      posClampedMs,
      pct,
      isYouTube,
      youtubeStreamUrl,
      youtubeDebugInfo,
      refresh,
      seekTo,
      pausePlayback,
      resumePlayback,
      setPlayerDock,
    }),
    [
      nowPlaying,
      queueState,
      paused,
      displayPosMs,
      displayDurationMs,
      posClampedMs,
      pct,
      isYouTube,
      youtubeStreamUrl,
      youtubeDebugInfo,
      refresh,
      pausePlayback,
      resumePlayback,
      setPlayerDock,
    ],
  );

  const portalTarget = playerHostRef.current;
  const isDocked = !!dockEl;

  return (
    <PlaybackContext.Provider value={ctxValue}>
      {props.children}
      {portalTarget
        ? createPortal(
            <audio
              ref={youtubeAudioRef}
              autoPlay
              preload="auto"
              src={youtubeStreamUrl}
              controls={isDocked}
              className={isDocked ? 'youtubeAudio youtubeAudioDocked' : 'youtubeAudio'}
              onLoadedMetadata={() => updateYoutubeDebug('loadedmetadata')}
              onPlaying={() => {
                updateYoutubeDebug('playing');
                youtubePlaybackStartedRef.current = true;
              }}
              onCanPlay={() => {
                if (paused) return;
                updateYoutubeDebug('canplay');
                try {
                  const a = youtubeAudioRef.current;
                  if (!a) return;
                  const p = a.play();
                  if (p && typeof (p as any).catch === 'function') {
                    (p as any).catch(() => {});
                  }
                } catch {
                }
              }}
              onTimeUpdate={() => updateYoutubeDebug('timeupdate')}
              onEnded={() => {
                updateYoutubeDebug('ended');
                if (!youtubePlaybackStartedRef.current) return;
                nextTrack().catch(() => {});
              }}
              onError={() => {
                updateYoutubeDebug('error');
                if (!youtubePlaybackStartedRef.current) return;
                nextTrack().catch(() => {});
              }}
            />,
            portalTarget,
          )
        : null}
    </PlaybackContext.Provider>
  );
}

export function MiniPlayer() {
  const { nowPlaying, isYouTube, durationMs, posClampedMs, seekTo, paused, pausePlayback, resumePlayback } = usePlayback();

  if (!isYouTube || !nowPlaying) return null;

  const name = typeof nowPlaying.name === 'string' && nowPlaying.name.trim() !== '' ? nowPlaying.name.trim() : null;
  const artists = Array.isArray(nowPlaying.artists)
    ? nowPlaying.artists.filter((v) => typeof v === 'string' && v.trim() !== '')
    : [];
  const subtitle = artists.length ? artists.join(', ') : null;
  const fallback = typeof nowPlaying.uri === 'string' && nowPlaying.uri.trim() !== '' ? nowPlaying.uri.trim() : 'YouTube track';

  return (
    <div className="headerMiniPlayer">
      <div className="headerMiniControls">
        <button type="button" className="miniControlBtn" onClick={() => resumePlayback()} disabled={!paused}>
          Play
        </button>
        <button type="button" className="miniControlBtn" onClick={() => pausePlayback()} disabled={paused}>
          Pause
        </button>
      </div>
      <div className="headerMiniInfo">
        <div className="headerMiniTitle">{name || fallback}</div>
        {subtitle ? <div className="headerMiniSubtitle">{subtitle}</div> : null}
      </div>
      <div className="headerMiniProgress">
        <input
          className="progressRange"
          type="range"
          min={0}
          max={durationMs || 0}
          value={posClampedMs || 0}
          onChange={(e) => seekTo(Number(e.target.value))}
          disabled={!durationMs}
        />
        <div className="headerMiniTime">
          <span>{fmtTime(posClampedMs)}</span>
          <span>{fmtTime(durationMs)}</span>
        </div>
      </div>
    </div>
  );
}

export function YouTubePlayerDock(props: { className?: string }) {
  const { setPlayerDock } = usePlayback();
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    setPlayerDock(ref.current);
    return () => {
      setPlayerDock(null);
    };
  }, [setPlayerDock]);

  return <div ref={ref} className={props.className ? `youtubePlayerDock ${props.className}` : 'youtubePlayerDock'} />;
}
