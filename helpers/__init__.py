import configparser
from pathlib import Path

from spotipy import Spotify, SpotifyPKCE

from utils.structured_logging import get_structured_logger
from utils.runtime_paths import ensure_parent_dir, get_config_path, get_spotipy_cache_path
from utils.spotify_errors import spotify_error_message
from .actions import Actions  # Expose Actions for external imports
from .checks import Checks  # Expose Checks for external imports

logger = get_structured_logger('tiptune.helpers')

config_path = get_config_path()
ensure_parent_dir(config_path)

config = configparser.ConfigParser()

_read_files = config.read(config_path)

sp_oauth = None
spotify_client = None
spotify_client_error = None


def refresh_spotify_client() -> None:
    global sp_oauth, spotify_client, spotify_client_error

    sp_oauth = None
    spotify_client = None
    spotify_client_error = None

    try:
        if not config.has_section("Spotify"):
            return

        client_id = config.get("Spotify", "client_id", fallback="").strip()
        redirect_url = config.get("Spotify", "redirect_url", fallback="").strip()

        if not client_id or not redirect_url:
            return

        cache_path = get_spotipy_cache_path()
        ensure_parent_dir(cache_path)

        sp_oauth = SpotifyPKCE(
            client_id=client_id,
            redirect_uri=redirect_url,
            scope="user-modify-playback-state user-read-playback-state user-read-currently-playing user-read-private",
            open_browser=False,
            cache_path=str(cache_path),
            requests_timeout=5,
        )

        token_info = sp_oauth.validate_token(sp_oauth.cache_handler.get_cached_token())

        if token_info is None:
            spotify_client_error = 'Spotify API authorization is missing or lacks permissions. Connect Spotify in Settings.'
            return

        spotify_client = Spotify(auth_manager=sp_oauth, requests_timeout=5)
    except Exception as exc:
        spotify_client_error = spotify_error_message(exc)
        logger.warning("spotify.init.error", message=spotify_client_error)
        sp_oauth = None
        spotify_client = None


refresh_spotify_client()
