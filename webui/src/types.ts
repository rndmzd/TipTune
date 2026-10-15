export type QueueItem = {
  requester?: string;
  queue_entry_id?: string;
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
  starting?: boolean;
  playback_error?: string | null;
  playback_device_name?: string;
  playback_device_id?: string;
  playback_device_explicit?: boolean;
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
  is_restricted?: boolean;
};

export type ObsStatus = {
  ok: true; enabled: boolean; connected?: boolean;
  overlay_url?: string; display_mode?: string; display_clients?: number;
  viewport?: { width: number; height: number };
  status?: { current_scene?: string | null; main_scene?: string | null };
  browser_source?: { name?: string; configured?: boolean; input_exists?: boolean; in_scene?: boolean; error?: string | null } | null;
  spotify_audio_capture?: ObsAudioCapture | null;
  tiptune_audio_capture?: ObsAudioCapture | null;
};

export type ObsAudioCapture = {
  target_exe?: string | null; input_name?: string | null; input_kind?: string | null;
  input_exists?: boolean; in_main_scene?: boolean; present?: boolean; configured?: boolean;
  target_matches?: boolean; muted?: boolean; scene_item_enabled?: boolean;
  state?: 'configured' | 'needs_setup' | 'muted' | 'disabled' | 'unavailable' | 'needs_desktop' | 'needs_monitoring' | 'device_missing';
  message?: string;
};
