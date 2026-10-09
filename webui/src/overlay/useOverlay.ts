import { useEffect, useRef, useState } from 'react';
import { sseUrl } from '../api';
import type { OverlayState } from './types';

export function useOverlay(enabled = true) {
  const [state, setState] = useState<OverlayState | null>(null);
  const [connected, setConnected] = useState(false);
  const [stale, setStale] = useState(true);
  const [tick, setTick] = useState(Date.now());
  const offset = useRef(0);
  useEffect(() => {
    if (!enabled) return;
    setStale(true); setConnected(false);
    let lastHeard = 0;
    const events = new EventSource(sseUrl('/api/overlay/events'));
    const heard = () => { lastHeard = Date.now(); setConnected(true); setStale(false); };
    events.addEventListener('snapshot', (event) => {
      try {
        const next = JSON.parse((event as MessageEvent).data) as OverlayState;
        if (next.schema_version !== 1 || !next.config || !Array.isArray(next.upcoming) || !Array.isArray(next.alerts)) return;
        offset.current = Date.now() - next.server_timestamp * 1000;
        heard();
        setState((previous) => previous?.session_id === next.session_id && previous.revision > next.revision ? previous : next);
      } catch { /* Retry on the next complete snapshot. */ }
    });
    events.addEventListener('heartbeat', heard);
    events.onerror = () => {
      setConnected(false);
      setState((previous) => previous ? { ...previous, alerts: [] } : null);
    };
    const timer = window.setInterval(() => {
      setTick(Date.now());
      if (Date.now() - lastHeard >= 15000) setStale(true);
    }, 250);
    return () => { events.close(); window.clearInterval(timer); };
  }, [enabled]);
  const visible = state && !stale ? {
    ...state, alerts: state.alerts.filter((alert) => alert.expires_at * 1000 + offset.current > tick),
  } : null;
  return { state: visible, connected, stale };
}
