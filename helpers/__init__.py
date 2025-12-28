import configparser
from pathlib import Path

from spotipy import Spotify, SpotifyOAuth

from utils.structured_logging import get_structured_logger
from .actions import Actions  # Expose Actions for external imports
from .checks import Checks  # Expose Checks for external imports

logger = get_structured_logger('mongobate.helpers')

config_path = Path(__file__).parent.parent / 'config.ini'

config = configparser.ConfigParser()

_read_files = config.read(config_path)
if not _read_files:
    raise SystemExit("config.ini not found. Copy config.ini.example to config.ini and fill in your credentials.")

sp_oauth = SpotifyOAuth(
    client_id=config.get("Spotify", "client_id"),
    client_secret=config.get("Spotify", "client_secret"),
    redirect_uri=config.get("Spotify", "redirect_url"),
    scope="user-modify-playback-state user-read-playback-state user-read-currently-playing user-read-private",
    open_browser=False
)
spotify_client = Spotify(auth_manager=sp_oauth)
