import asyncio
from typing import List, Optional

from chatdj.chatdj import SongRequest
from utils.structured_logging import get_structured_logger

logger = get_structured_logger('tiptune.helpers.actions')

class Actions:
    def __init__(self,
                 chatdj: bool = False,
                 obs_integration: bool = False):

        from . import config
        self.config = config

        self.chatdj_enabled = bool(chatdj)
        self.obs_integration_enabled = bool(obs_integration)
        self.request_overlay_duration = 10

        logger.info("actions.init",
                   message="Initializing actions",
                   data={
                       "chatdj": chatdj,
                       "obs_integration": obs_integration
                   })

        # Initialize components based on flags
        if self.chatdj_enabled:
            logger.debug("actions.chatdj.init", message="Initializing ChatDJ")
            try:
                from chatdj import AutoDJ, SongExtractor
                from . import spotify_client
            except Exception as exc:
                logger.exception("actions.chatdj.init.error", message="Failed to import ChatDJ components", exc=exc)
                self.chatdj_enabled = False
            else:
                openai_api_key = config.get("OpenAI", "api_key", fallback="").strip()
                if openai_api_key in ("", "your-openai-api-key"):
                    logger.warning("actions.chatdj.disabled", message="OpenAI API key not configured")
                    self.chatdj_enabled = False
                else:
                    google_api_key_raw = config.get("Search", "google_api_key", fallback="").strip()
                    google_cx_raw = config.get("Search", "google_cx", fallback="").strip()
                    google_api_key = google_api_key_raw if google_api_key_raw else None
                    google_cx = google_cx_raw if google_cx_raw else None

                    model = config.get("OpenAI", "model", fallback="gpt-5").strip() or "gpt-5"

                    try:
                        self.song_extractor = SongExtractor(
                            openai_api_key,
                            spotify_client=spotify_client,
                            google_api_key=google_api_key,
                            google_cx=google_cx,
                            model=model
                        )
                    except Exception as exc:
                        logger.exception("actions.chatdj.init.error", message="Failed to initialize ChatDJ", exc=exc)
                        self.chatdj_enabled = False
                    else:
                        if spotify_client is not None:
                            playback_device_id_raw = config.get("Spotify", "playback_device_id", fallback="").strip()
                            playback_device_id = playback_device_id_raw if playback_device_id_raw else None
                            try:
                                self.auto_dj = AutoDJ(spotify_client, playback_device_id=playback_device_id)
                            except Exception as exc:
                                logger.exception("actions.chatdj.init.error", message="Failed to initialize Spotify AutoDJ", exc=exc)

        if self.obs_integration_enabled:
            logger.debug("actions.obs.init", message="Initializing OBS integration")
            try:
                from handlers.obshandler import OBSHandler

                host = config.get("OBS", "host", fallback="localhost").strip() or "localhost"
                try:
                    port = config.getint("OBS", "port", fallback=4455)
                except Exception:
                    port = 4455
                if not isinstance(port, int) or port <= 0:
                    port = 4455

                password = config.get("OBS", "password", fallback=None)
                if isinstance(password, str) and password.strip() == "":
                    password = None

                self.obs = OBSHandler(
                    host=host,
                    port=port,
                    password=password
                )
                # Not connecting yet
                logger.info("actions.obs.init.complete", message="OBS handler initialized")

                try:
                    self.request_overlay_duration = config.getint("General", "request_overlay_duration", fallback=10)
                except Exception:
                    self.request_overlay_duration = 10
            except Exception as exc:
                logger.exception("actions.obs.init.error", message="Failed to initialize OBS integration", exc=exc)
                self.obs_integration_enabled = False

    async def extract_song_titles(self, message: str, song_count: int) -> List[SongRequest]:
        """Extract song titles from a message using SongExtractor (running in executor)."""
        if not self.chatdj_enabled:
            logger.warning("chatdj.disabled", message="ChatDJ not enabled")
            return []

        loop = asyncio.get_running_loop()
        # Run blocking extraction in executor to avoid blocking the event loop
        return await loop.run_in_executor(None, self.song_extractor.extract_songs, message, song_count)

    async def find_song_spotify(self, song_info: SongRequest) -> Optional[str]:
        """Return the spotify_uri provided in the song_info."""
        if not self.chatdj_enabled:
            return None
        if not hasattr(self, 'auto_dj'):
            return None

        logger.debug("spotify.search.start",
                    message="Starting Spotify song search",
                    data={
                        "song": song_info.song,
                        "artist": song_info.artist
                    })

        # Wrapping potential blocking call
        loop = asyncio.get_running_loop()
        search_result = await loop.run_in_executor(None, self.auto_dj.search_track_uri, song_info.song, song_info.artist)

        if search_result:
            logger.debug("spotify.search.success",
                        message="Found Spotify track",
                        data={"uri": search_result})
            return search_result
        else:
            logger.warning("spotify.search.notfound",
                         message="No Spotify URI found for song")
            return None

    async def available_in_market(self, song_uri: str) -> bool:
        """Check if a song is available in the user's market."""
        if not self.chatdj_enabled:
            return False
        if not hasattr(self, 'auto_dj'):
            return False

        try:
            logger.debug("spotify.market.check.start", message="Checking market availability", data={"uri": song_uri})

            loop = asyncio.get_running_loop()
            user_market = await loop.run_in_executor(None, self.auto_dj.get_user_market)
            song_markets = await loop.run_in_executor(None, self.auto_dj.get_song_markets, song_uri)

            is_available = (user_market in song_markets) or song_markets == []
            logger.debug("spotify.market.check.complete",
                        data={"is_available": is_available})
            return is_available

        except Exception as exc:
            logger.exception("spotify.market.error", message="Failed to check market availability", exc=exc)
            return False

    async def add_song_to_queue(self, uri: str, requester_name: str, song_details: str) -> bool:
        """Add a song to the playback queue and trigger the song requester overlay."""
        if not self.chatdj_enabled:
            return False
        if not hasattr(self, 'auto_dj'):
            return False

        logger.debug("spotify.queue.add.start",
                    message="Adding song to queue",
                    data={"uri": uri, "requester": requester_name, "song": song_details})

        try:
            loop = asyncio.get_running_loop()
            queue_result = await loop.run_in_executor(None, self.auto_dj.add_song_to_queue, uri)

            if queue_result:
                logger.debug("spotify.queue.add.success", message="Successfully added song to queue")

                # Should be async call now
                await self.trigger_song_requester_overlay(
                    requester_name,
                    song_details,
                    self.request_overlay_duration if self.request_overlay_duration else 10
                )
                return True

            logger.error("spotify.queue.add.failed", message="Failed to add song to queue")
            return False

        except Exception as exc:
            logger.exception("spotify.queue.error", message="Failed to add song to queue", exc=exc)
            return False

    async def skip_song(self) -> bool:
        """Skip the currently playing song."""
        if not self.chatdj_enabled:
            return False
        if not hasattr(self, 'auto_dj'):
            return False

        logger.debug("spotify.playback.skip.start", message="Attempting to skip current song")
        try:
            loop = asyncio.get_running_loop()
            skip_result = await loop.run_in_executor(None, self.auto_dj.skip_song)

            if skip_result:
                logger.info("spotify.playback.skip.success", message="Successfully skipped current song")
            return skip_result

        except Exception as exc:
            logger.exception("spotify.playback.error", message="Failed to skip song", exc=exc)
            return False

    async def trigger_song_requester_overlay(self, requester: str, song: str, duration: int) -> None:
        if not self.obs_integration_enabled:
            return
        await self.obs.trigger_song_requester_overlay(requester, song, duration)

    async def trigger_warning_overlay(self, username: str, message: str, duration: int) -> None:
        if not self.obs_integration_enabled:
            return
        await self.obs.trigger_warning_overlay(username, message, duration)

    async def trigger_queue_state_overlay(self, message: str, duration: Optional[int] = None) -> None:
        if not self.obs_integration_enabled:
            return
        obs = getattr(self, 'obs', None)
        if obs is None:
            return
        if duration is None:
            try:
                duration = int(getattr(self, 'request_overlay_duration', 10) or 10)
            except Exception:
                duration = 10
        try:
            duration = max(1, int(duration))
        except Exception:
            duration = 10
        try:
            await obs.trigger_motor_overlay(message, overlay_type='processing', display_duration=duration)
        except Exception as exc:
            logger.exception("obs.overlay.queue_state.error", message="Failed to trigger queue state overlay", exc=exc)
