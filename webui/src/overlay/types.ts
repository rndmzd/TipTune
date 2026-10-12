import type { QueueItem } from '../types';

export const overlayDefaults: Record<string, string> = {
  mode: 'browser', layout: 'full', position: 'bottom-left', scale: '100', margin: '32',
  queue_length: '3', show_now_playing: 'true', show_queue: 'true', show_alerts: 'true',
  show_artwork: 'true', accent_color: '#5b8cff', background_color: '#111318',
  text_color: '#f2f3f5', opacity: '90', motion: 'subtle', source_name: 'TipTune Overlay',
  show_advertisements: 'false', advertisement_interval: '300', advertisement_duration: '15',
};

export type OverlayAdvertisement = { id: string; created_at: number; expires_at: number };
export type OverlayRequestInfo = { song_cost: number; skip_song_cost: number };
export const defaultRequestInfo: OverlayRequestInfo = { song_cost: 27, skip_song_cost: 51 };

export function requestInfoFromConfig(general: Record<string, string> = {}): OverlayRequestInfo {
  const price = (key: keyof OverlayRequestInfo) => {
    const raw = general[key]?.trim() || '';
    const value = /^[+]?\d+$/.test(raw) ? Number(raw) : NaN;
    return Number.isInteger(value) && value > 0 ? value : defaultRequestInfo[key];
  };
  return { song_cost: price('song_cost'), skip_song_cost: price('skip_song_cost') };
}

export type OverlayAlert = {
  id: string; kind: string; message: string; requester?: string | null;
  created_at: number; expires_at: number;
};
export type OverlayState = {
  schema_version: number; session_id: string; revision: number; server_timestamp: number;
  config: Record<string, string>; now_playing: QueueItem | null; upcoming: QueueItem[];
  total_queue_count: number;
  status: { queue_paused: boolean; playback_paused: boolean; starting: boolean };
  alerts: OverlayAlert[];
  request_info?: OverlayRequestInfo;
  advertisement?: OverlayAdvertisement | null;
};

export function sampleState(): OverlayState {
  return {
    schema_version: 1, session_id: 'sample', revision: 0, server_timestamp: Date.now() / 1000,
    config: overlayDefaults,
    now_playing: { queue_entry_id: 'sample-current', source: 'spotify', name: 'Midnight City', artists: ['M83'], requester: 'StarGazer' },
    upcoming: [
      { queue_entry_id: 'sample-1', source: 'youtube', name: 'A Moment Apart', artists: ['ODESZA'], requester: 'Moonwalker' },
      { queue_entry_id: 'sample-2', source: 'spotify', name: 'Dreams', artists: ['Fleetwood Mac'], requester: 'NightOwl' },
      { queue_entry_id: 'sample-3', source: 'spotify', name: 'Something About Us', artists: ['Daft Punk'] },
    ], total_queue_count: 3,
    status: { queue_paused: false, playback_paused: false, starting: false }, alerts: [],
    request_info: defaultRequestInfo, advertisement: null,
  };
}
