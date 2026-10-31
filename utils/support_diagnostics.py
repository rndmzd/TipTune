"""Small, explicit diagnostics schemas shared by logs, History, and Sentry."""

from contextvars import ContextVar
from dataclasses import dataclass
from functools import lru_cache
from importlib import metadata
import json
import platform
import re
import sys
import uuid

from utils.runtime_paths import get_resource_path


SUPPORT_ID = re.compile(r'^TT-[a-f0-9]{32}$')
VERSION = re.compile(r'^[0-9]+(?:\.[0-9]+)*(?:[.-]?(?:a|b|alpha|beta|rc|post|dev)[.-]?[0-9]+)?(?:\+[0-9]+)?$')
DEPENDENCIES = ('openai', 'httpx', 'httpx2', 'aiohttp', 'spotipy', 'sentry-sdk')
FAILURE_CODES = frozenset((
    'song.extract.failed', 'song.lookup.failed', 'song.availability.failed', 'song.queue.failed',
    'spotify.playback.unconfirmed', 'spotify.playback.failed',
    'spotify.playback.unavailable', 'spotify.playback.invalid_track',
))
SUPPORT_OPERATION = ContextVar('tiptune_support_operation', default=None)


@dataclass
class SupportOperation:
    owner: object
    support_id: str
    active: bool = True
    error_code: str | None = None
    report_id: str | None = None
    trace_id: str | None = None
    report_state: str = 'not_reported'


def valid_support_id(value):
    return value if isinstance(value, str) and SUPPORT_ID.fullmatch(value) else None


def new_support_id():
    return 'TT-' + uuid.uuid4().hex


def current_support_id():
    current = SUPPORT_OPERATION.get()
    return current.support_id if current is not None and current.active else None


@lru_cache(maxsize=1)
def dependency_versions():
    versions = {}
    for name in DEPENDENCIES:
        try:
            value = metadata.version(name)
        except metadata.PackageNotFoundError:
            continue
        if VERSION.fullmatch(value):
            versions[name] = value
    return versions


@lru_cache(maxsize=1)
def installed_webview2_version():
    """Installed Evergreen runtime version, when Windows exposes it; no registry dump."""
    if sys.platform != 'win32':
        return None
    import winreg
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
            try:
                with winreg.OpenKey(hive, r'SOFTWARE\Microsoft\EdgeUpdate\Clients', 0, winreg.KEY_READ | view) as clients:
                    for index in range(min(winreg.QueryInfoKey(clients)[0], 100)):
                        try:
                            with winreg.OpenKey(clients, winreg.EnumKey(clients, index)) as client:
                                if winreg.QueryValueEx(client, 'name')[0] == 'Microsoft Edge WebView2 Runtime':
                                    version = winreg.QueryValueEx(client, 'pv')[0]
                                    if isinstance(version, str) and re.fullmatch(r'[0-9]+(?:\.[0-9]+){1,3}', version):
                                        return version
                        except OSError:
                            continue
            except OSError:
                continue
    return None


def sanitize_environment(value):
    """Only versions, build identifiers, enums, booleans, and a random session ID."""
    if not isinstance(value, dict):
        return {}
    result = {}
    for key in ('app_version', 'python_version', 'webview2_installed_version', 'os_build'):
        item = value.get(key)
        if isinstance(item, str) and VERSION.fullmatch(item):
            result[key] = item
    for key, pattern in (('commit', r'[a-f0-9]{40}(?:[a-f0-9]{24})?'),
                         ('build_id', r'[a-f0-9]{64}'), ('session_id', r'[a-f0-9]{32}')):
        item = value.get(key)
        if isinstance(item, str) and re.fullmatch(pattern, item):
            result[key] = item
    for key, allowed in (
        ('build_kind', ('source', 'packaged')),
        ('architecture', ('x86', 'x86_64', 'aarch64', 'arm', 'unknown')),
        ('music_source', ('spotify', 'youtube')),
        ('overlay_mode', ('browser', 'legacy', 'both')),
        ('playback_backend', ('spotify_embedded', 'spotify_external', 'youtube_browser', 'unknown')),
    ):
        if value.get(key) in allowed:
            result[key] = value[key]
    for key in ('source_dirty', 'obs_enabled'):
        if type(value.get(key)) is bool:
            result[key] = value[key]
    dependencies = value.get('dependencies')
    if isinstance(dependencies, dict):
        result['dependencies'] = {name: version for name, version in dependencies.items()
                                  if name in DEPENDENCIES and isinstance(version, str) and VERSION.fullmatch(version)}
    return result


@lru_cache(maxsize=1)
def runtime_environment():
    if getattr(sys, 'frozen', False):
        try:
            build = json.loads(get_resource_path('build-info.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            build = {}
    else:
        build = {'build_kind': 'source', 'dependencies': dependency_versions()}
    result = sanitize_environment(build)
    machine = platform.machine().lower()
    result.update({'architecture': {'amd64': 'x86_64', 'x86_64': 'x86_64', 'x86': 'x86',
                                    'i386': 'x86', 'i686': 'x86', 'arm64': 'aarch64',
                                    'aarch64': 'aarch64', 'arm': 'arm'}.get(machine, 'unknown'),
                   'python_version': platform.python_version(), 'os_build': platform.version()})
    version = installed_webview2_version()
    if version:
        result['webview2_installed_version'] = version
    return sanitize_environment(result)


def environment_for_report(config, version, session_id):
    result = {**runtime_environment(), 'app_version': version, 'session_id': session_id}
    for section, option, key in (('Music', 'source', 'music_source'), ('Overlay', 'mode', 'overlay_mode')):
        result[key] = config.get(section, option, fallback='')
    try:
        if config.has_option('OBS', 'enabled'):
            result['obs_enabled'] = config.getboolean('OBS', 'enabled')
    except ValueError:
        pass
    return sanitize_environment(result)
