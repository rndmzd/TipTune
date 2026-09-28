import json
import time
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from spotipy import Spotify, SpotifyException

from utils.structured_logging import get_structured_logger
from utils.runtime_paths import ensure_dir, get_cache_dir

from .song_requests import (SongRequest, SongExtractor, SongExtractionError, SongResolutionError,
                            account_market, collect_candidates, exact_candidate)

logger = get_structured_logger('tiptune.chatdj.chatdj')


class AutoDJ:
    """AutoDJ class using Spotify APIs.
    Song interpretation and catalog matching live in song_requests, independent of playback."""
    def __init__(self, spotify: Spotify, playback_device_id: Optional[str] = None):
        self.spotify = spotify
        self.playback_device: Optional[str] = None
        self.playback_device_name: Optional[str] = None

        if playback_device_id:
            try:
                self.set_playback_device(playback_device_id, silent=True)
            except Exception:
                pass

        if not self.playback_device:
            try:
                device = self._auto_select_playback_device()
                if device and isinstance(device, dict):
                    self.playback_device = device.get('id')
                    self.playback_device_name = device.get('name')
                    if self.playback_device:
                        logger.info(
                            "spotify.device.selected",
                            message="Device auto-selected",
                            data={
                                "name": self.playback_device_name,
                                "id": self.playback_device,
                                "auto": True
                            }
                        )
            except Exception:
                pass

        if not self.playback_device:
            selected_device_id = self._select_playback_device()
            self.playback_device = selected_device_id
            try:
                for d in self.get_available_devices():
                    if isinstance(d, dict) and d.get('id') == selected_device_id:
                        self.playback_device_name = d.get('name')
                        break
            except Exception:
                pass
            self.set_playback_device(selected_device_id, silent=True)

        logger.debug("spotify.playback.init", message="Initializing playback state")
        self.playing_first_track = False
        self._last_start_playback_ts = 0.0

        self._queue_unpaused = threading.Event()
        self._queue_unpaused.set()

        self._queue_lock = threading.Lock()

        self.queued_tracks = []
        self.now_playing_track_uri = None
        self.clear_playback_context(persist=False)
        try:
            self._restore_persisted_queue_state()
        except Exception:
            pass
        self._print_variables()

    def persist_queue_state(self) -> None:
        self._persist_queue_state()

    def _queue_persist_path(self) -> Path:
        base = get_cache_dir('TipTune')
        ensure_dir(base)
        return base / 'queue_state.json'

    def _queue_state_snapshot(self) -> Dict[str, Any]:
        try:
            paused = self.queue_paused()
        except Exception:
            paused = False

        with self._queue_lock:
            queued_tracks = list(self.queued_tracks)

        now_playing = getattr(self, 'now_playing_track_uri', None)
        if not isinstance(now_playing, str) or now_playing.strip() == '':
            now_playing = None

        return {
            'v': 1,
            'ts': time.time(),
            'paused': bool(paused),
            'now_playing_track_uri': now_playing,
            'queued_tracks': queued_tracks,
        }

    def _persist_queue_state(self) -> None:
        try:
            path = self._queue_persist_path()
            payload = self._queue_state_snapshot()
            tmp = path.with_suffix(path.suffix + '.tmp')
            tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
            tmp.replace(path)
        except Exception as exc:
            logger.exception("queue.persist.error", message="Failed to persist queue state", exc=exc)

    def _restore_persisted_queue_state(self) -> None:
        path = self._queue_persist_path()
        if not path.exists():
            return

        try:
            raw = path.read_text(encoding='utf-8', errors='replace')
            payload = json.loads(raw)
        except Exception:
            return

        if not isinstance(payload, dict):
            return

        queued_raw = payload.get('queued_tracks', [])
        if not isinstance(queued_raw, list):
            queued_raw = []

        queued_tracks: list[str] = []
        for item in queued_raw:
            if isinstance(item, str) and item.strip() != '':
                queued_tracks.append(item)

        paused = bool(payload.get('paused', False))
        now_playing = payload.get('now_playing_track_uri', None)
        if not isinstance(now_playing, str) or now_playing.strip() == '':
            now_playing = None

        with self._queue_lock:
            self.queued_tracks = list(queued_tracks)

        self.now_playing_track_uri = now_playing
        if paused:
            try:
                self._queue_unpaused.clear()
            except Exception:
                pass
        else:
            try:
                self._queue_unpaused.set()
            except Exception:
                pass

        if queued_tracks:
            try:
                self.playing_first_track = True
            except Exception:
                pass

    def get_queued_tracks_snapshot(self) -> List[Any]:
        try:
            with self._queue_lock:
                return list(self.queued_tracks)
        except Exception:
            return []

    def move_queued_track(self, from_index: int, to_index: int) -> bool:
        try:
            fi = int(from_index)
            ti = int(to_index)
        except Exception:
            return False

        try:
            with self._queue_lock:
                n = len(self.queued_tracks)
                if fi < 0 or fi >= n:
                    return False
                if ti < 0 or ti >= n:
                    return False
                if fi == ti:
                    return True
                item = self.queued_tracks.pop(fi)
                self.queued_tracks.insert(ti, item)
            self._persist_queue_state()
            self._print_variables(True)
            return True
        except Exception as exc:
            logger.exception("queue.move.error", message="Failed to move queued track", exc=exc)
            return False

    def delete_queued_track(self, index: int) -> bool:
        try:
            idx = int(index)
        except Exception:
            return False

        try:
            with self._queue_lock:
                n = len(self.queued_tracks)
                if idx < 0 or idx >= n:
                    return False
                _ = self.queued_tracks.pop(idx)
            self._persist_queue_state()
            self._print_variables(True)
            return True
        except Exception as exc:
            logger.exception("queue.delete.error", message="Failed to delete queued track", exc=exc)
            return False

    def get_available_devices(self) -> List[Dict[str, Any]]:
        try:
            payload = self.spotify.devices()
            devices = payload.get('devices', []) if isinstance(payload, dict) else []
            if isinstance(devices, list):
                return devices
            return []
        except Exception as exc:
            logger.exception("spotify.devices.error", message="Failed to list Spotify devices", exc=exc)
            return []

    def _auto_select_playback_device(self) -> Optional[Dict[str, Any]]:
        devices = self.get_available_devices()
        if not devices:
            return None
        active = [d for d in devices if isinstance(d, dict) and d.get('is_active')]
        if active:
            return active[0]
        return devices[0] if isinstance(devices[0], dict) else None

    def set_playback_device(self, device_id: str, force_play: bool = False, silent: bool = False) -> bool:
        try:
            if not device_id:
                return False

            self.spotify.transfer_playback(device_id=device_id, force_play=force_play)
            self.playback_device = device_id

            try:
                for d in self.get_available_devices():
                    if isinstance(d, dict) and d.get('id') == device_id:
                        self.playback_device_name = d.get('name')
                        break
            except Exception:
                pass

            if not silent:
                logger.info(
                    "spotify.device.selected",
                    message="Device selected",
                    data={
                        "name": self.playback_device_name,
                        "id": self.playback_device,
                        "auto": False
                    }
                )
            return True
        except Exception as exc:
            logger.exception("spotify.device.error", message="Failed to set playback device", exc=exc)
            return False

    def _print_variables(self, return_value=None):
        """Stub function for logging internal state."""

    def _select_playback_device(self) -> str:
        try:
            devices = self.spotify.devices()['devices']
            logger.debug(f"Available devices: {devices}")
            if not devices:
                logger.error("spotify.devices.error",
                           message="No Spotify devices found")
                raise ValueError("No available Spotify devices found.")
            print("\n==[ Available Spotify Devices ]==\n")
            for idx, device in enumerate(devices):
                print(f"{idx+1} - {device['name']}")
            while True:
                try:
                    selection = int(input("\nChoose playback device number: "))
                    device = devices[selection - 1]
                    self.playback_device_name = device.get('name')
                    logger.info("spotify.device.selected",
                              message="Device selected",
                              data={
                                  "name": device['name'],
                                  "id": device['id']
                              })
                    return device['id']
                except KeyboardInterrupt:
                    logger.info("spotify.device.cancel",
                              message="User cancelled device selection")
                    raise
                except (ValueError, IndexError):
                    logger.error("spotify.device.error",
                               message="Invalid device selection")
                    print("Invalid selection. Please try again.")
        except Exception as e:
            logger.exception("spotify.device.error",
                            message="Failed to select playback device",
                            exc=e)

            raise

    def search_track_uri(self, song: str, artist: str) -> Optional[str]:
        """Compatibility helper for exact catalog lookup; request resolution uses SongExtractor."""
        tracks = collect_candidates(self.spotify, song, artist, account_market(self.spotify))
        match = exact_candidate(tracks, song, artist)
        return match['uri'] if match else None

    def queue_paused(self) -> bool:
        return not self._queue_unpaused.is_set()

    def pause_queue(self, silent: bool = False) -> bool:
        try:
            self._queue_unpaused.clear()
            self._persist_queue_state()
            if not silent:
                logger.info(
                    "queue.pause",
                    message="Queue paused. Current song will be allowed to finish; next songs will wait until resumed.",
                    data={"queued_tracks": len(self.queued_tracks)}
                )
            return True
        except Exception as exc:
            logger.exception("queue.pause.error", message="Failed to pause queue", exc=exc)
            return False

    def unpause_queue(self, silent: bool = False) -> bool:
        try:
            self._queue_unpaused.set()
            self._persist_queue_state()
            if not silent:
                logger.info(
                    "queue.unpause",
                    message="Queue resumed.",
                    data={"queued_tracks": len(self.queued_tracks)}
                )
            return True
        except Exception as exc:
            logger.exception("queue.unpause.error", message="Failed to resume queue", exc=exc)
            return False


    def add_song_to_queue(self, track_uri: str, silent=False) -> bool:
        try:
            if not silent:
                logger.debug("spotify.queue.add",
                            message="Adding track to queue",
                            data={"track_uri": track_uri})

            with self._queue_lock:
                self.queued_tracks.append(track_uri)
                qlen = len(self.queued_tracks)

            self._persist_queue_state()

            if not self.playback_active() and qlen == 1:
                self.playing_first_track = True
            logger.debug("spotify.queue.status",
                        message="Current queue status",
                        data={"queued_tracks": self.queued_tracks})
            self._print_variables(True)
            return True
        except SpotifyException as e:
            logger.exception("spotify.queue.add.error",
                message="Failed to add song to queue",
                exc=e)
            return False

    def insert_song_to_queue(self, track_uri: str, index: int = 0, silent: bool = False) -> bool:
        try:
            if not silent:
                logger.debug(
                    "spotify.queue.insert",
                    message="Inserting track into queue",
                    data={"track_uri": track_uri, "index": index},
                )

            with self._queue_lock:
                n = len(self.queued_tracks)
                try:
                    idx = int(index)
                except Exception:
                    idx = 0
                if idx < 0:
                    idx = 0
                if idx > n:
                    idx = n
                self.queued_tracks.insert(idx, track_uri)
                qlen = len(self.queued_tracks)

            self._persist_queue_state()

            if not self.playback_active() and qlen == 1:
                self.playing_first_track = True

            logger.debug(
                "spotify.queue.status",
                message="Current queue status",
                data={"queued_tracks": self.queued_tracks},
            )
            self._print_variables(True)
            return True
        except SpotifyException as e:
            logger.exception(
                "spotify.queue.insert.error",
                message="Failed to insert song into queue",
                exc=e,
            )
            return False

    def check_queue_status(self, silent=False) -> bool:
        try:
            if not self.playback_active():
                paused = self.queue_paused()
                with self._queue_lock:
                    qlen = len(self.queued_tracks)

                if qlen == 0:
                    self.now_playing_track_uri = None

                if paused and qlen > 0:
                    self.now_playing_track_uri = None
                    if not silent:
                        logger.info(
                            "queue.check.paused",
                            message="Queue is paused. Waiting to resume before starting the next song.",
                            data={"queued_tracks": qlen}
                        )
                    self._print_variables(False)
                    return False
                if qlen > 0:
                    if self.now_playing_track_uri and not paused:
                        try:
                            is_playing, item_uri, progress_ms, duration_ms = self._get_playback_snapshot()
                        except Exception:
                            is_playing = False
                            item_uri = None
                            progress_ms = None
                            duration_ms = None

                        try:
                            since_start = time.time() - float(getattr(self, '_last_start_playback_ts', 0.0) or 0.0)
                        except Exception:
                            since_start = 9999.0

                        # Spotify Desktop can sometimes report is_playing=false while keeping the same item URI,
                        # and even resetting progress_ms near 0 after a track ends. Only treat low progress as a
                        # transient "starting" state if we very recently started playback ourselves.
                        if (
                            (not is_playing)
                            and since_start < 20.0
                            and item_uri == self.now_playing_track_uri
                            and isinstance(progress_ms, (int, float))
                            and progress_ms < 15000
                        ):
                            if not silent:
                                logger.debug(
                                    "queue.check.transient_inactive",
                                    message="Playback reported inactive, but current track appears to be starting; not advancing queue.",
                                    data={"progress_ms": progress_ms, "track_uri": item_uri, "queued_tracks": qlen, "since_start": since_start},
                                )
                            self._print_variables(False)
                            return False

                    try:
                        since = time.time() - float(getattr(self, '_last_start_playback_ts', 0.0) or 0.0)
                    except Exception:
                        since = 9999.0

                    if self.now_playing_track_uri and since < 12.0:
                        if not silent:
                            logger.debug(
                                "queue.check.cooldown",
                                message="Playback recently started; waiting before starting next track.",
                                data={"seconds_since_start": since, "queued_tracks": qlen},
                            )
                        self._print_variables(False)
                        return False

                    logger.info("queue.check.start",
                                message="Queue populated but playback is not active. Starting playback.")
                    with self._queue_lock:
                        if not self.queued_tracks:
                            return False
                        popped_track = self.queued_tracks.pop(0)
                        self.now_playing_track_uri = popped_track
                    self._persist_queue_state()
                    logger.debug("queue.check.popped",
                                message=f"Popped track: {popped_track}")
                    self.spotify.start_playback(device_id=self.playback_device, uris=[popped_track])
                    try:
                        self._last_start_playback_ts = time.time()
                    except Exception:
                        self._last_start_playback_ts = 0.0
                    logger.debug("queue.check.playing_first_track",
                                message="Clearing playing_first_track flag.")
                    self.playing_first_track = False
                    return True
                self._print_variables(False)

                if qlen == 0 and not self.playing_first_track:
                    logger.info("queue.check.empty",
                                message="Queue is now empty")
                    self.playing_first_track = True

                return False

            with self._queue_lock:
                first_queued = self.queued_tracks[0] if self.queued_tracks else None

            paused = self.queue_paused()
            if paused:
                self._print_variables(True)
                return True

            if first_queued:
                is_playing, current_track, progress_ms, duration_ms = self._get_playback_snapshot()
                logger.debug(f"Current playing track: {current_track}")
                if current_track == first_queued:
                    logger.info(f"Now playing queued track: {current_track}")
                    with self._queue_lock:
                        if self.queued_tracks and self.queued_tracks[0] == current_track:
                            popped_track = self.queued_tracks.pop(0)
                            self.now_playing_track_uri = popped_track
                        else:
                            popped_track = None
                    self._persist_queue_state()
                    logger.debug("queue.check.popped",
                                message=f"Popped track: {popped_track}")
            self._print_variables(True)
            return True
        except SpotifyException as exc:
            logger.exception("queue.check.error",
                            message="Failed to check queue status",
                            exc=exc)
            return False
        except Exception as exc:
            logger.exception("queue.check.error",
                            message="Failed to check queue status",
                            exc=exc)
            return False


    def start_track(self, track_uri: str) -> bool:
        """Start a request and confirm the device actually began playing it."""
        self.spotify.start_playback(device_id=self.playback_device, uris=[track_uri])
        for attempt in range(11):
            if attempt:
                time.sleep(0.5)
            playback = self.spotify.current_playback() or {}
            item = playback.get('item') or {}
            device = playback.get('device') or {}
            # Spotify may relink a track to an equivalent available recording.
            original_uri = (item.get('linked_from') or {}).get('uri')
            if (
                playback.get('is_playing')
                and track_uri in (item.get('uri'), original_uri)
                and (not self.playback_device or device.get('id') == self.playback_device)
            ):
                self.now_playing_track_uri = item.get('uri') or track_uri
                self._last_start_playback_ts = time.time()
                return True
        logger.warning(
            "spotify.playback.unconfirmed",
            message="Spotify accepted the request but did not start the track on the selected device; keeping the request queued.",
            data={"track_uri": track_uri, "device_id": self.playback_device},
        )
        return False

    def clear_playback_context(self, persist: bool = True) -> bool:
        try:
            logger.info("Clearing playback context.")
            try:
                queue = self.spotify.queue()
                spotify_queue_len = len((queue or {}).get('queue') or [])
                logger.debug(
                    "queue.clear.spotify_queue",
                    message="Read Spotify queue state (TipTune does not attempt to clear it by skipping)",
                    data={"spotify_queue_len": spotify_queue_len},
                )
            except Exception:
                pass

            try:
                playback = self.spotify.current_playback() or {}
                if playback.get('is_playing'):
                    self.spotify.pause_playback(device_id=self.playback_device)
                    logger.info("queue.clear.pause",
                                message="Playback paused.")
            except SpotifyException as exc:
                logger.error("queue.clear.error",
                            message=f"Error pausing playback: {exc}")
            self.playing_first_track = False

            self.now_playing_track_uri = None

            with self._queue_lock:
                self.queued_tracks.clear()
            if persist:
                self._persist_queue_state()
            self._print_variables(True)
            return True


        except SpotifyException as exc:
            logger.exception("queue.clear.error",
                            message="Failed to clear playback context.",
                            exc=exc)

            return False


    def get_user_market(self) -> Optional[str]:
        try:
            user_info = self.spotify.me()
            logger.debug("spotify.user.info",
                        message="Retrieved user market information",
                        data={"user_info": user_info})
            return user_info['country']
        except SpotifyException as exc:
            logger.exception("spotify.user.error",
                           message="Failed to get user market",
                           exc=exc)
            return None

    def get_song_markets(self, track_uri: str) -> List[str]:
        try:
            track_info = self.spotify.track(track_uri)
            logger.debug("spotify.track.info",
                        message="Retrieved track market information",
                        data={
                            "track_uri": track_uri,
                            "track_info": track_info
                        })
            return track_info.get('available_markets', []) or []
        except SpotifyException as exc:
            logger.exception("spotify.track.error",
                           message="Failed to get song markets",
                           exc=exc,
                           data={"track_uri": track_uri})
            return []

    def playback_active(self) -> bool:
        try:
            is_playing, item_uri, progress_ms, duration_ms = self._get_playback_snapshot()

            now_uri = getattr(self, 'now_playing_track_uri', None)
            has_match = (
                isinstance(now_uri, str)
                and now_uri.strip() != ''
                and isinstance(item_uri, str)
                and item_uri == now_uri
            )

            if has_match and isinstance(progress_ms, int) and isinstance(duration_ms, int) and duration_ms > 0:
                remaining_ms = duration_ms - progress_ms
                if remaining_ms <= 2500:
                    logger.debug(
                        "spotify.playback.status",
                        message="Playback near end; treating as inactive so queue can advance",
                        data={
                            "track_uri": item_uri,
                            "progress_ms": progress_ms,
                            "duration_ms": duration_ms,
                            "remaining_ms": remaining_ms,
                            "is_playing": is_playing,
                        },
                    )
                    return False

            if is_playing:
                logger.debug(
                    "spotify.playback.status",
                    message="Playback is active",
                    data={"is_playing": True, "track_uri": item_uri},
                )
                return True

            if has_match and isinstance(progress_ms, int) and isinstance(duration_ms, int) and duration_ms > 0:
                logger.debug(
                    "spotify.playback.status",
                    message="Playback paused; treating as active",
                    data={
                        "track_uri": item_uri,
                        "progress_ms": progress_ms,
                        "duration_ms": duration_ms,
                    },
                )
                return True

            return False
        except SpotifyException as exc:
            logger.exception("spotify.playback.error",
                           message="Error checking playback state",
                           exc=exc)
            return False
        except Exception as exc:
            logger.exception("spotify.playback.error",
                           message="Error checking playback state",
                           exc=exc)
            return False

    def _get_playback_snapshot(self) -> tuple[bool, Optional[str], Optional[int], Optional[int]]:
        try:
            pb = self.spotify.current_playback()
        except Exception:
            pb = None

        if not isinstance(pb, dict):
            return (False, None, None, None)

        is_playing = bool(pb.get('is_playing'))

        progress_ms: Optional[int] = None
        try:
            if pb.get('progress_ms') is not None:
                progress_ms = int(pb.get('progress_ms'))
        except Exception:
            progress_ms = None

        item_uri: Optional[str] = None
        duration_ms: Optional[int] = None
        item = pb.get('item')
        if isinstance(item, dict):
            uri = item.get('uri')
            if isinstance(uri, str) and uri.strip() != '':
                item_uri = uri

            try:
                if item.get('duration_ms') is not None:
                    duration_ms = int(item.get('duration_ms'))
            except Exception:
                duration_ms = None

        return (is_playing, item_uri, progress_ms, duration_ms)

    def skip_song(self, silent=False) -> bool:
        try:
            if not silent:
                logger.info("spotify.playback.skip",
                           message="Skipping current track",
                           data={
                               "device_id": self.playback_device
                           })
            self.spotify.next_track(device_id=self.playback_device)
            return True

        except SpotifyException as exc:
            if not silent:
                logger.exception("spotify.playback.error",
                               message="Failed to skip track",
                               exc=exc,
                               data={
                                   "device_id": self.playback_device
                               })
            return False

