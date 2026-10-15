import { invoke } from '@tauri-apps/api/core';

// Startup, route cleanup, placement and reload must share one queue. Otherwise
// a late cleanup can park a player that the next route has just displayed.
let pending: Promise<unknown> = Promise.resolve();
export function playerCommand(command: 'spotify_player_initialize' | 'spotify_player_show' | 'spotify_player_hide' | 'spotify_player_reload' | 'audio_output_configure', args?: Record<string, unknown>) {
  const next = pending.catch(() => {}).then(() => invoke(command, args));
  pending = next;
  return next;
}
