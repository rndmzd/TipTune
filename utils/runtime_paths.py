import os
import sys
from pathlib import Path
from typing import Optional


def is_frozen() -> bool:
    return bool(getattr(sys, 'frozen', False))


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def resource_root() -> Path:
    meipass = getattr(sys, '_MEIPASS', None)
    if meipass:
        return Path(meipass)
    return _repo_root()


def get_resource_path(*parts: str) -> Path:
    return resource_root().joinpath(*parts)


def _windows_known_folder(env_name: str, fallback_parts: list[str]) -> Path:
    raw = os.getenv(env_name)
    if raw:
        return Path(raw)
    return Path.home().joinpath(*fallback_parts)


def user_config_dir(app_name: str = 'TipTune') -> Path:
    base = _windows_known_folder('APPDATA', ['AppData', 'Roaming'])
    return base / app_name


def user_cache_dir(app_name: str = 'TipTune') -> Path:
    base = _windows_known_folder('LOCALAPPDATA', ['AppData', 'Local'])
    return base / app_name / '.cache'


def get_config_path(app_name: str = 'TipTune') -> Path:
    override = os.getenv('TIPTUNE_CONFIG')
    if override:
        return Path(override)

    if is_frozen():
        portable = Path(sys.executable).resolve().parent / 'config.ini'
        if portable.exists():
            return portable

        return user_config_dir(app_name) / 'config.ini'

    return _repo_root() / 'config.ini'


def get_cache_dir(app_name: str = 'TipTune') -> Path:
    override = os.getenv('TIPTUNE_CACHE_DIR')
    if override:
        return Path(override)

    if is_frozen():
        return user_cache_dir(app_name)

    return _repo_root() / '.cache'


def get_spotipy_cache_path(app_name: str = 'TipTune') -> Path:
    override = os.getenv('TIPTUNE_SPOTIPY_CACHE')
    if override:
        return Path(override)

    cfg = get_config_path(app_name)
    return cfg.with_name('.cache')


def ensure_parent_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def read_text_if_exists(path: Path, encoding: str = 'utf-8') -> Optional[str]:
    try:
        if not path.exists():
            return None
        return path.read_text(encoding=encoding, errors='replace')
    except Exception:
        return None
