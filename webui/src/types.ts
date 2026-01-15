export type QueueItem = {
  source?: string;
  uri?: string;
  track_id?: string;
  name?: string;
  artists?: string[];
  album?: string;
  duration_ms?: number;
  explicit?: boolean;
  spotify_url?: string;
  external_url?: string;
  album_image_url?: string;
};

export type QueueState = {
  enabled?: boolean;
  source?: string;
  paused?: boolean;
  playback_device_name?: string;
  playback_device_id?: string;
  playback_progress_ms?: number | null;
  playback_is_playing?: boolean | null;
  playback_track_uri?: string | null;
  now_playing_item?: QueueItem;
  now_playing_track?: any;
  queued_items?: QueueItem[];
  queued_tracks?: any[];
};

export type Device = {
  id?: string;
  name?: string;
  is_active?: boolean;
};
