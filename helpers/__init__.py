import configparser
from pathlib import Path

from spotipy import Spotify, SpotifyOAuth

from utils.structured_logging import get_structured_logger
from utils.runtime_paths import ensure_parent_dir, get_config_path, get_spotipy_cache_path
from .actions import Actions  # Expose Actions for external imports
from .checks import Checks  # Expose Checks for external imports

logger = get_structured_logger('tiptune.helpers')

config_path = get_config_path()
ensure_parent_dir(config_path)

config = configparser.ConfigParser()

_read_files = config.read(config_path)

sp_oauth = None
spotify_client = None


def refresh_spotify_client() -> None:
    global sp_oauth, spotify_client

    sp_oauth = None
    spotify_client = None

    try:
        if not config.has_section("Spotify"):
            return

        client_id = config.get("Spotify", "client_id", fallback="").strip()
        client_secret = config.get("Spotify", "client_secret", fallback="").strip()
        redirect_url = config.get("Spotify", "redirect_url", fallback="").strip()

        if not client_id or not client_secret or not redirect_url:
            return

        cache_path = get_spotipy_cache_path()
        ensure_parent_dir(cache_path)

        sp_oauth = SpotifyOAuth(
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_url,
            scope="user-modify-playback-state user-read-playback-state user-read-currently-playing user-read-private",
            open_browser=False,
            cache_path=str(cache_path)
        )

        token_info = None
        try:
            token_info = sp_oauth.validate_token(sp_oauth.cache_handler.get_cached_token())
        except Exception:
            token_info = None

        if token_info is None:
            return

        spotify_client = Spotify(auth_manager=sp_oauth)
    except Exception as exc:
        logger.exception("spotify.init.error", message="Failed to initialize Spotify client", exc=exc)
        sp_oauth = None
        spotify_client = None


refresh_spotify_client()
