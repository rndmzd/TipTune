import os
import sys
from functools import lru_cache
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


def platform_key() -> str:
    p = sys.platform
    if p.startswith('win'):
        return 'windows'
    if p == 'darwin':
        return 'macos'
    return 'linux'


def get_bundled_bin_base_dir() -> Optional[Path]:
    raw = os.getenv('TIPTUNE_RESOURCE_DIR')
    if raw:
        rd = Path(raw)
        candidates = [rd / 'bin', rd / 'resources' / 'bin']
        for c in candidates:
            try:
                if c.exists():
                    return c
            except Exception:
                continue
        return candidates[-1]

    return _repo_root() / 'src-tauri' / 'resources' / 'bin'


def get_bundled_bin_dir() -> Optional[Path]:
    base = get_bundled_bin_base_dir()
    if base is None:
        return None
    return base / platform_key()


def get_bundled_bin_path(name: str) -> Optional[Path]:
    d = get_bundled_bin_dir()
    if d is None:
        return None
    ext = '.exe' if platform_key() == 'windows' and not name.lower().endswith('.exe') else ''
    return d / f"{name}{ext}"


@lru_cache(maxsize=32)
def find_bundled_bin_path(name: str) -> Optional[Path]:
    p = get_bundled_bin_path(name)
    if p is not None:
        try:
            if p.exists():
                return p
        except Exception:
            pass

    ext = '.exe' if platform_key() == 'windows' and not str(name).lower().endswith('.exe') else ''
    filename = f"{name}{ext}"

    candidates: list[Path] = []
    try:
        rr = resource_root()
        candidates += [
            rr / 'bin',
            rr / 'resources' / 'bin',
            rr / 'resources' / 'resources' / 'bin',
        ]
    except Exception:
        pass

    try:
        exe_dir = Path(sys.executable).resolve().parent
        candidates.append(exe_dir / 'bin')
        candidates.append(exe_dir / 'resources' / 'bin')
    except Exception:
        pass

    for base in candidates:
        try:
            plat_dir = base / platform_key()
            for c in (plat_dir / filename, base / filename):
                try:
                    if c.exists():
                        return c
                except Exception:
                    continue
        except Exception:
            continue

    return None
