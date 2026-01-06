import asyncio
import configparser
import json
import logging
import os
import secrets
import signal
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional, List, Tuple
from urllib.parse import urlparse

import httpx
from aiohttp import web

from chatdj.chatdj import SongRequest
from helpers.actions import Actions
from helpers.checks import Checks
from utils.runtime_paths import ensure_parent_dir, get_config_path, get_resource_path, get_spotipy_cache_path
from utils.structured_logging import get_structured_logger, StructuredLogFormatter

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

config_path = get_config_path()
ensure_parent_dir(config_path)

config = configparser.ConfigParser()
config.read(config_path)

logger = get_structured_logger('tiptune.app')
shutdown_event: asyncio.Event = asyncio.Event()


def _setup_logging() -> None:
    root = logging.getLogger()
    if root.handlers:
        return

    level_name = str(os.getenv('TIPTUNE_LOG_LEVEL', 'INFO') or 'INFO').strip().upper()
    level = getattr(logging, level_name, logging.INFO)
    root.setLevel(level)

    sh = logging.StreamHandler(sys.stdout)
    sh.setLevel(level)
    sh.setFormatter(StructuredLogFormatter())
    root.addHandler(sh)

    log_path = os.getenv('TIPTUNE_LOG_PATH')
    if isinstance(log_path, str) and log_path.strip():
        try:
            p = Path(log_path.strip())
            ensure_parent_dir(p)
            fh = logging.FileHandler(p, encoding='utf-8')
            fh.setLevel(level)
            fh.setFormatter(StructuredLogFormatter())
            root.addHandler(fh)
        except Exception:
            pass


_setup_logging()


async def _watch_parent_process() -> None:
    pid_str = os.getenv('TIPTUNE_PARENT_PID')
    if not pid_str:
        return

    try:
        parent_pid = int(pid_str)
        if parent_pid <= 0:
            return
    except Exception:
        return

    while not shutdown_event.is_set():
        await asyncio.sleep(1.5)

        try:
            if sys.platform == 'win32':
                import ctypes
                PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
                handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, parent_pid)
                if handle:
                    ctypes.windll.kernel32.CloseHandle(handle)
                else:
                    shutdown_event.set()
                    break
            else:
                os.kill(parent_pid, 0)
        except Exception:
            shutdown_event.set()
            break


def _get_web_runtime_overrides() -> Tuple[Optional[str], Optional[int]]:
    host_env = os.getenv('TIPTUNE_WEB_HOST')
    host: Optional[str]
    if isinstance(host_env, str) and host_env.strip():
        host = host_env.strip()
    else:
        host = None

    port: Optional[int] = None
    port_env = os.getenv('TIPTUNE_WEB_PORT')
    if isinstance(port_env, str) and port_env.strip():
        try:
            port_val = int(port_env.strip())
            if 0 <= port_val <= 65535:
                port = port_val
        except Exception:
            port = None

    argv: List[str] = list(sys.argv[1:])
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg.startswith('--web-host='):
            value = arg.split('=', 1)[1].strip()
            host = value if value else None
        elif arg == '--web-host' and i + 1 < len(argv):
            value = argv[i + 1].strip()
            host = value if value else None
            i += 1
        elif arg.startswith('--web-port='):
            value = arg.split('=', 1)[1].strip()
            try:
                port_val = int(value)
                if 0 <= port_val <= 65535:
                    port = port_val
            except Exception:
                pass
        elif arg == '--web-port' and i + 1 < len(argv):
            value = argv[i + 1].strip()
            try:
                port_val = int(value)
                if 0 <= port_val <= 65535:
                    port = port_val
            except Exception:
                pass
            i += 1
        i += 1

    return host, port


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        v = value.strip().lower()
        if v in ('1', 'true', 'yes', 'y', 'on'):
            return True
        if v in ('0', 'false', 'no', 'n', 'off'):
            return False
    return default


def _is_secret_field(section: str, key: str) -> bool:
    k = (key or '').strip().lower()
    s = (section or '').strip().lower()
    if k in ('api_key', 'client_secret', 'google_api_key', 'password'):
        return True
    if s == 'events api' and k == 'url':
        return True
    if 'secret' in k or 'token' in k:
        return True
    return False


def _is_setup_complete(cfg: Optional[configparser.ConfigParser] = None) -> bool:
    try:
        src = cfg if cfg is not None else config
        if not src.has_section("General"):
            return False
        return src.getboolean("General", "setup_complete", fallback=False)
    except Exception:
        return False


def _update_ini_file(path: Path, updates: Dict[str, Dict[str, str]]) -> None:
    if not path.exists():
        example_path = path.with_name(path.name + '.example')
        bundled_example_path = get_resource_path('config.ini.example')
        try:
            if example_path.exists():
                path.write_text(example_path.read_text(encoding='utf-8', errors='replace'), encoding='utf-8')
            elif bundled_example_path.exists():
                path.write_text(bundled_example_path.read_text(encoding='utf-8', errors='replace'), encoding='utf-8')
            else:
                path.write_text('', encoding='utf-8')
        except Exception:
            path.write_text('', encoding='utf-8')

    lines = path.read_text(encoding='utf-8', errors='replace').splitlines(keepends=True)

    def find_section_bounds(section_name: str) -> Optional[tuple[int, int]]:
        header = f'[{section_name}]'
        start = None
        for idx, line in enumerate(lines):
            if line.strip() == header:
                start = idx
                break
        if start is None:
            return None
        end = len(lines)
        for idx in range(start + 1, len(lines)):
            if lines[idx].lstrip().startswith('[') and lines[idx].rstrip().endswith(']'):
                end = idx
                break
        return (start, end)

    for section, section_updates in updates.items():
        if not isinstance(section_updates, dict):
            continue

        bounds = find_section_bounds(section)
        if bounds is None:
            if lines and not lines[-1].endswith('\n'):
                lines[-1] = lines[-1] + '\n'
            if lines and lines[-1].strip() != '':
                lines.append('\n')
            lines.append(f'[{section}]\n')
            lines.append('\n')
            bounds = find_section_bounds(section)
            if bounds is None:
                continue

        section_start, section_end = bounds

        for key, value in section_updates.items():
            key_str = str(key)
            found_idx = None
            for idx in range(section_start + 1, section_end):
                line = lines[idx]
                stripped = line.strip()
                if stripped.startswith('#') or stripped.startswith(';') or stripped == '':
                    continue
                if '=' not in line and ':' not in line:
                    continue

                if '=' in line:
                    delim = '='
                else:
                    delim = ':'

                left, _right = line.split(delim, 1)
                if left.strip().lower() == key_str.strip().lower():
                    found_idx = idx
                    prefix = left.rstrip(' ') + delim
                    lines[idx] = f'{prefix} {value}\n'
                    break

            if found_idx is None:
                insert_at = section_end
                while insert_at > section_start + 1 and lines[insert_at - 1].strip() == '':
                    insert_at -= 1
                lines.insert(insert_at, f'{key_str} = {value}\n')
                section_end += 1

    path.write_text(''.join(lines), encoding='utf-8')


class WebUI:
    def __init__(self, service: 'SongRequestService', host: str = '127.0.0.1', port: int = 8765):
        self._service = service
        self._host = host
        self._port = int(port)

        @web.middleware
        async def _cors_middleware(request: web.Request, handler):
            if request.method == 'OPTIONS':
                resp = web.Response(status=200)
            else:
                resp = await handler(request)

            origin = request.headers.get('Origin')
            if origin:
                resp.headers['Access-Control-Allow-Origin'] = origin
                resp.headers['Vary'] = 'Origin'
                resp.headers['Access-Control-Allow-Methods'] = 'GET,POST,OPTIONS'
                resp.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization, Access-Control-Request-Private-Network'

            if request.headers.get('Access-Control-Request-Private-Network') == 'true':
                resp.headers['Access-Control-Allow-Private-Network'] = 'true'
            return resp

        self._app = web.Application(middlewares=[_cors_middleware])
        self._runner: Optional[web.AppRunner] = None
        self._site: Optional[web.TCPSite] = None

        self._webui_root = get_resource_path('webui')
        self._dist_root = self._webui_root / 'dist'
        self._spa_index = self._dist_root / 'index.html'

        assets_dir = self._dist_root / 'assets'
        if self._spa_index.exists() and assets_dir.exists():
            self._app.router.add_static('/assets', str(assets_dir), show_index=False)

        self._app.add_routes([
            web.get('/', self._page_app),
            web.get('/settings', self._page_app),
            web.get('/setup', self._page_app),
            web.get('/events', self._page_app),
            web.get('/api/queue', self._api_queue),
            web.post('/api/queue/add', self._api_queue_add),
            web.post('/api/queue/pause', self._api_pause),
            web.post('/api/queue/resume', self._api_resume),
            web.post('/api/queue/move', self._api_queue_move),
            web.post('/api/queue/delete', self._api_queue_delete),
            web.get('/api/obs/status', self._api_obs_status),
            web.post('/api/obs/ensure_sources', self._api_obs_ensure_sources),
            web.post('/api/obs/ensure_spotify_audio_capture', self._api_obs_ensure_spotify_audio_capture),
            web.post('/api/obs/now_playing', self._api_obs_now_playing),
            web.post('/api/obs/test_overlay', self._api_obs_test_overlay),
            web.get('/api/spotify/devices', self._api_devices),
            web.get('/api/spotify/search', self._api_spotify_search),
            web.post('/api/spotify/device', self._api_set_device),
            web.get('/api/spotify/auth/status', self._api_spotify_auth_status),
            web.post('/api/spotify/auth/start', self._api_spotify_auth_start),
            web.get('/api/setup/status', self._api_setup_status),
            web.get('/api/config', self._api_get_config),
            web.post('/api/config', self._api_update_config),
            web.get('/api/events/recent', self._api_events_recent),
            web.get('/api/events/sse', self._api_events_sse),
        ])

        # SPA fallback for client-side routes
        self._app.add_routes([
            web.get('/{path:.*}', self._page_app),
        ])

    async def start(self) -> None:
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, host=self._host, port=self._port)
        await self._site.start()

    async def stop(self) -> None:
        if self._runner:
            await self._runner.cleanup()
        self._runner = None
        self._site = None

    async def _page_app(self, request: web.Request) -> web.Response:
        force_dashboard = _as_bool(request.query.get('dashboard'), default=False)
        if request.path != '/setup' and not force_dashboard and not _is_setup_complete():
            raise web.HTTPFound('/setup')

        if self._spa_index.exists():
            html = self._spa_index.read_text(encoding='utf-8', errors='replace')
            return web.Response(text=html, content_type='text/html', headers={"Cache-Control": "no-store"})

        msg = (
            "Web UI is not built.\n\n"
            "To build the WebUI, run: npm run webui:build\n"
            "Then restart TipTune.\n"
        )
        return web.Response(text=msg, content_type='text/plain', status=503, headers={"Cache-Control": "no-store"})

    async def _api_queue(self, _request: web.Request) -> web.Response:
        try:
            queue = await self._service.get_queue_state()
            return web.json_response({"ok": True, "queue": queue})
        except Exception as exc:
            logger.exception("webui.api.queue.error", exc=exc, message="Failed to get queue state")
            return web.json_response({"ok": False, "error": str(exc)})

    async def _api_pause(self, _request: web.Request) -> web.Response:
        ok = await self._service.pause_queue()
        return web.json_response({"ok": bool(ok)})

    async def _api_resume(self, _request: web.Request) -> web.Response:
        ok = await self._service.resume_queue()
        return web.json_response({"ok": bool(ok)})

    async def _api_queue_move(self, request: web.Request) -> web.Response:
        try:
            payload = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "Invalid JSON"}, status=400)

        if not isinstance(payload, dict):
            return web.json_response({"ok": False, "error": "Invalid JSON"}, status=400)

        try:
            from_index = int(payload.get('from_index'))
            to_index = int(payload.get('to_index'))
        except Exception:
            return web.json_response({"ok": False, "error": "Invalid indices"}, status=400)

        ok = await self._service.move_queue_item(from_index, to_index)
        if not ok:
            return web.json_response({"ok": False, "error": "Failed to move queue item"}, status=400)
        return web.json_response({"ok": True})

    async def _api_queue_delete(self, request: web.Request) -> web.Response:
        try:
            payload = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "Invalid JSON"}, status=400)

        if not isinstance(payload, dict):
            return web.json_response({"ok": False, "error": "Invalid JSON"}, status=400)

        try:
            index = int(payload.get('index'))
        except Exception:
            return web.json_response({"ok": False, "error": "Invalid index"}, status=400)

        ok = await self._service.delete_queue_item(index)
        if not ok:
            return web.json_response({"ok": False, "error": "Failed to delete queue item"}, status=400)
        return web.json_response({"ok": True})

    async def _api_queue_add(self, request: web.Request) -> web.Response:
        try:
            payload = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "Invalid JSON"}, status=400)

        if not isinstance(payload, dict):
            return web.json_response({"ok": False, "error": "Invalid JSON"}, status=400)

        uri = payload.get('uri')
        if not isinstance(uri, str) or uri.strip() == "":
            return web.json_response({"ok": False, "error": "Invalid uri"}, status=400)

        if 'index' in payload:
            index_raw = payload.get('index')
            try:
                index = int(index_raw)
            except Exception:
                return web.json_response({"ok": False, "error": "Invalid index"}, status=400)

            ok = await self._service.insert_track_to_queue(uri, index=index)
        else:
            ok = await self._service.add_track_to_queue(uri)

        if not ok:
            return web.json_response({"ok": False, "error": "Failed to add track to queue"}, status=400)
        return web.json_response({"ok": True})

    async def _api_devices(self, _request: web.Request) -> web.Response:
        try:
            devices, error = await self._service.get_spotify_devices()
            payload: Dict[str, Any] = {"ok": True, "devices": devices}
            if error:
                payload["error"] = error
            return web.json_response(payload)
        except Exception as exc:
            logger.exception("webui.api.devices.error", exc=exc, message="Failed to get devices")
            return web.json_response({"ok": False, "error": str(exc), "devices": []})

    async def _api_spotify_search(self, request: web.Request) -> web.Response:
        q = request.query.get('q', '')
        q = q.strip() if isinstance(q, str) else ''
        if q == '' or len(q) < 2:
            return web.json_response({"ok": False, "error": "Query too short"}, status=400)

        limit_raw = request.query.get('limit', '10')
        try:
            limit = int(limit_raw)
        except Exception:
            limit = 10
        if limit <= 0:
            limit = 10
        limit = min(limit, 25)

        try:
            tracks = await self._service.search_spotify_tracks(q, limit=limit)
            return web.json_response({"ok": True, "tracks": tracks})
        except Exception as exc:
            logger.exception("webui.api.spotify.search.error", exc=exc, message="Failed to search Spotify")
            return web.json_response({"ok": False, "error": str(exc), "tracks": []}, status=400)

    async def _api_set_device(self, request: web.Request) -> web.Response:
        try:
            payload = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "Invalid JSON"}, status=400)

        device_id = payload.get('device_id') if isinstance(payload, dict) else None
        persist = _as_bool(payload.get('persist'), default=True) if isinstance(payload, dict) else True
        ok = await self._service.set_spotify_device(device_id, persist=persist)
        if not ok:
            return web.json_response({"ok": False, "error": "Failed to set device"}, status=400)
        return web.json_response({"ok": True})

    async def _api_spotify_auth_status(self, _request: web.Request) -> web.Response:
        try:
            status = await self._service.get_spotify_auth_status()
            return web.json_response({"ok": True, **status})
        except Exception as exc:
            logger.exception("webui.api.spotify.auth.status.error", exc=exc, message="Failed to get Spotify auth status")
            return web.json_response({"ok": False, "error": str(exc)})

    async def _api_spotify_auth_start(self, _request: web.Request) -> web.Response:
        try:
            ok, auth_url, error = await self._service.start_spotify_auth()
            if not ok or not auth_url:
                return web.json_response({"ok": False, "error": error or "Failed to start Spotify auth"}, status=400)
            return web.json_response({"ok": True, "auth_url": auth_url})
        except Exception as exc:
            logger.exception("webui.api.spotify.auth.start.error", exc=exc, message="Failed to start Spotify auth")
            return web.json_response({"ok": False, "error": str(exc)})

    async def _api_get_config(self, _request: web.Request) -> web.Response:
        try:
            return web.json_response({"ok": True, "config": self._service.get_config_for_ui()})
        except Exception as exc:
            logger.exception("webui.api.config.error", exc=exc, message="Failed to read config for UI")
            return web.json_response({"ok": False, "error": str(exc), "config": {}})

    async def _api_setup_status(self, _request: web.Request) -> web.Response:
        try:
            fresh_config = configparser.ConfigParser()
            fresh_config.read(config_path)

            events_url = ""
            if fresh_config.has_section("Events API"):
                events_url = fresh_config.get("Events API", "url", fallback="").strip()
            events_configured = bool(events_url) and "yourusername" not in events_url and "your-token" not in events_url

            openai_api_key = ""
            if fresh_config.has_section("OpenAI"):
                openai_api_key = fresh_config.get("OpenAI", "api_key", fallback="").strip()
            openai_configured = bool(openai_api_key) and openai_api_key not in ("your-openai-api-key",)

            google_api_key = ""
            google_cx = ""
            if fresh_config.has_section("Search"):
                google_api_key = fresh_config.get("Search", "google_api_key", fallback="").strip()
                google_cx = fresh_config.get("Search", "google_cx", fallback="").strip()
            google_configured = bool(google_api_key) and bool(google_cx)

            return web.json_response({
                "ok": True,
                "setup_complete": _is_setup_complete(fresh_config),
                "events_configured": events_configured,
                "openai_configured": openai_configured,
                "google_configured": google_configured,
            })
        except Exception as exc:
            logger.exception("webui.api.setup_status.error", exc=exc, message="Failed to compute setup status")
            return web.json_response({"ok": False, "error": str(exc)})

    async def _api_update_config(self, request: web.Request) -> web.Response:
        try:
            payload = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "Invalid JSON"}, status=400)

        ok, error = await self._service.update_config_from_ui(payload)
        if not ok:
            return web.json_response({"ok": False, "error": error or "update failed"}, status=400)
        return web.json_response({"ok": True})

    async def _api_obs_status(self, _request: web.Request) -> web.Response:
        try:
            data = await self._service.get_obs_status()
            return web.json_response({"ok": True, **data})
        except Exception as exc:
            logger.exception("webui.api.obs_status.error", exc=exc, message="Failed to get OBS status")
            return web.json_response({"ok": False, "error": str(exc)}, status=500)

    async def _api_obs_ensure_sources(self, _request: web.Request) -> web.Response:
        try:
            result = await self._service.ensure_obs_text_sources()
            if result is None:
                return web.json_response({"ok": False, "error": "OBS not available"}, status=400)
            return web.json_response({"ok": True, "result": result})
        except Exception as exc:
            logger.exception("webui.api.obs_ensure_sources.error", exc=exc, message="Failed to ensure OBS sources")
            return web.json_response({"ok": False, "error": str(exc)}, status=500)

    async def _api_obs_ensure_spotify_audio_capture(self, _request: web.Request) -> web.Response:
        try:
            result = await self._service.ensure_obs_spotify_audio_capture()
            if result is None:
                return web.json_response({"ok": False, "error": "OBS not available"}, status=400)
            return web.json_response({"ok": True, "result": result})
        except Exception as exc:
            logger.exception("webui.api.obs_ensure_spotify_audio_capture.error", exc=exc, message="Failed to ensure Spotify audio capture")
            return web.json_response({"ok": False, "error": str(exc)}, status=500)

    async def _api_obs_test_overlay(self, request: web.Request) -> web.Response:
        try:
            payload = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "Invalid JSON"}, status=400)
        if not isinstance(payload, dict):
            return web.json_response({"ok": False, "error": "Invalid JSON"}, status=400)

        overlay = payload.get('overlay')
        ok, error = await self._service.trigger_obs_test_overlay(overlay)
        if not ok:
            return web.json_response({"ok": False, "error": error or "Failed to trigger overlay"}, status=400)
        return web.json_response({"ok": True})

    async def _api_obs_now_playing(self, _request: web.Request) -> web.Response:
        ok, error = await self._service.trigger_obs_now_playing_overlay()
        if not ok:
            return web.json_response({"ok": False, "error": error or "Failed to trigger overlay"}, status=400)
        return web.json_response({"ok": True})

    async def _api_events_recent(self, request: web.Request) -> web.Response:
        limit_raw = request.query.get('limit', '50')
        try:
            limit = max(1, min(500, int(limit_raw)))
        except Exception:
            limit = 50
        return web.json_response({"ok": True, "events": self._service.get_recent_events(limit=limit)})

    async def _api_events_sse(self, request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse(status=200, reason='OK', headers={
            'Content-Type': 'text/event-stream',
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
        })

        origin = request.headers.get('Origin')
        if origin:
            resp.headers['Access-Control-Allow-Origin'] = origin
            resp.headers['Vary'] = 'Origin'
            resp.headers['Access-Control-Allow-Methods'] = 'GET,POST,OPTIONS'
            resp.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization, Access-Control-Request-Private-Network'

        if request.headers.get('Access-Control-Request-Private-Network') == 'true':
            resp.headers['Access-Control-Allow-Private-Network'] = 'true'

        resp.headers['X-Accel-Buffering'] = 'no'
        await resp.prepare(request)

        q_events = self._service.register_events_subscriber()

        try:
            try:
                await resp.write(b': connected\n\n')
            except (ConnectionResetError, BrokenPipeError):
                return resp

            while True:
                try:
                    item = await asyncio.wait_for(q_events.get(), timeout=15)
                except asyncio.TimeoutError:
                    transport = request.transport
                    if transport is None or transport.is_closing():
                        break
                    try:
                        await resp.write(b': ping\n\n')
                    except (ConnectionResetError, BrokenPipeError):
                        break
                    continue

                transport = request.transport
                if transport is None or transport.is_closing():
                    break

                data = json.dumps(item, default=str)
                try:
                    await resp.write(f'data: {data}\n\n'.encode('utf-8'))
                except (ConnectionResetError, BrokenPipeError):
                    break
            return resp
        except asyncio.CancelledError:
            return resp
        except Exception:
            return resp
        finally:
            self._service.unregister_events_subscriber(q_events)
            try:
                await resp.write_eof()
            except (ConnectionResetError, BrokenPipeError):
                pass


def handle_exception(_loop, context):
    if shutdown_event.is_set():
        return
    msg = context.get("exception", context.get("message"))
    logger.error("app.error",
                 message="Caught exception in event loop",
                 data={"error": str(msg)})


class EventsAPIClient:
    def __init__(self, start_url: str, max_requests_per_minute: int = 1000):
        self._next_url = start_url
        rpm = max(1, int(max_requests_per_minute))
        self._poll_interval_seconds = 60 / (rpm / 10)

    @property
    def poll_interval_seconds(self) -> float:
        return self._poll_interval_seconds

    async def poll(self, client: httpx.AsyncClient) -> list[dict]:
        resp = await client.get(self._next_url, timeout=30)
        resp.raise_for_status()
        payload = resp.json()

        events = payload.get("events", [])
        if isinstance(events, list):
            self._next_url = payload.get("nextUrl", self._next_url)
            return events

        return []


class SongRequestService:
    def __init__(self):
        self.checks = Checks()

        obs_enabled = config.getboolean("OBS", "enabled", fallback=True) if config.has_section("OBS") else False
        self.actions = Actions(
            chatdj=True,
            obs_integration=obs_enabled
        )

        self._tip_queue: asyncio.Queue[Dict[str, Any]] = asyncio.Queue(maxsize=1000)

        self._stop_event = asyncio.Event()
        self._tasks: list[asyncio.Task] = []

        self._events_recent: list[dict] = []
        self._events_recent_max = 500
        self._events_subscribers: set[asyncio.Queue] = set()

        self._track_cache: Dict[str, Dict[str, Any]] = {}
        self._track_cache_ttl_seconds = 6 * 60 * 60
        self._track_cache_max_items = 500

        self._web: Optional[WebUI] = None

        self._spotify_auth_lock = asyncio.Lock()
        self._spotify_auth_in_progress: bool = False
        self._spotify_auth_error: Optional[str] = None
        self._spotify_auth_url: Optional[str] = None
        self._spotify_auth_state: Optional[str] = None
        self._spotify_auth_oauth: Any = None
        self._spotify_auth_runner: Optional[web.AppRunner] = None
        self._spotify_auth_site: Optional[web.BaseSite] = None

    def _get_obs_overlay_duration_seconds(self) -> int:
        try:
            return max(1, int(config.getint("General", "request_overlay_duration", fallback=10)))
        except Exception:
            return 10

    async def get_obs_status(self) -> Dict[str, Any]:
        desired_enabled = config.getboolean("OBS", "enabled", fallback=True) if config.has_section("OBS") else False
        if not desired_enabled:
            return {"enabled": False}

        try:
            await self._refresh_obs_integration_from_config()
        except Exception:
            pass

        obs = getattr(self.actions, 'obs', None)
        if obs is None or not getattr(self.actions, 'obs_integration_enabled', False):
            return {"enabled": True, "connected": False}

        status = await obs.get_text_source_status(scene_key='main')
        if status is None:
            return {"enabled": True, "connected": False}

        spotify_audio_capture = None
        try:
            spotify_audio_capture = await obs.get_spotify_audio_capture_status(scene_key='main', exe_name='Spotify.exe')
        except Exception:
            spotify_audio_capture = None

        return {"enabled": True, "connected": True, "status": status, "spotify_audio_capture": spotify_audio_capture}

    async def ensure_obs_text_sources(self) -> Optional[Dict[str, Any]]:
        desired_enabled = config.getboolean("OBS", "enabled", fallback=True) if config.has_section("OBS") else False
        if not desired_enabled:
            return None

        try:
            await self._refresh_obs_integration_from_config()
        except Exception:
            pass

        obs = getattr(self.actions, 'obs', None)
        if obs is None or not getattr(self.actions, 'obs_integration_enabled', False):
            return None
        return await obs.ensure_text_sources(scene_key='main')

    async def ensure_obs_spotify_audio_capture(self) -> Optional[Dict[str, Any]]:
        desired_enabled = config.getboolean("OBS", "enabled", fallback=True) if config.has_section("OBS") else False
        if not desired_enabled:
            return None

        try:
            await self._refresh_obs_integration_from_config()
        except Exception:
            pass

        obs = getattr(self.actions, 'obs', None)
        if obs is None or not getattr(self.actions, 'obs_integration_enabled', False):
            return None

        return await obs.ensure_spotify_audio_capture(scene_key='main', exe_name='Spotify.exe', preferred_input_name='Spotify Audio')

    async def trigger_obs_test_overlay(self, overlay: Any) -> tuple[bool, Optional[str]]:
        desired_enabled = config.getboolean("OBS", "enabled", fallback=True) if config.has_section("OBS") else False
        if not desired_enabled:
            return (False, "OBS is disabled")

        try:
            await self._refresh_obs_integration_from_config()
        except Exception:
            pass

        obs = getattr(self.actions, 'obs', None)
        if obs is None or not getattr(self.actions, 'obs_integration_enabled', False):
            return (False, "OBS is not available")

        overlay_name = str(overlay or '').strip()
        duration = self._get_obs_overlay_duration_seconds()

        overlay_methods: dict[str, str] = {
            'SongRequester': 'trigger_song_requester_overlay',
            'WarningOverlay': 'trigger_warning_overlay',
            'GeneralOverlay': 'trigger_motor_overlay',
        }

        if overlay_name not in overlay_methods:
            return (False, "Unknown overlay")

        required_method = overlay_methods[overlay_name]
        if not hasattr(obs, required_method):
            return (False, f"OBS handler does not support {overlay_name}")

        async def _run() -> None:
            try:
                if overlay_name == 'SongRequester':
                    await obs.trigger_song_requester_overlay('TestUser', 'Test Song - Test Artist', duration)
                elif overlay_name == 'WarningOverlay':
                    await obs.trigger_warning_overlay('TestUser', 'This is a test warning overlay.', duration)
                elif overlay_name == 'GeneralOverlay':
                    await obs.trigger_motor_overlay('This is a test general overlay.', overlay_type='processing', display_duration=duration)
                else:
                    raise ValueError('Unknown overlay')
            except Exception as exc:
                logger.exception(
                    "obs.test_overlay.error",
                    message="Test overlay task failed",
                    exc=exc,
                    data={"overlay": overlay_name}
                )

        try:
            asyncio.create_task(_run())
        except Exception as exc:
            return (False, str(exc))

        return (True, None)

    async def trigger_obs_now_playing_overlay(self) -> tuple[bool, Optional[str]]:
        desired_enabled = config.getboolean("OBS", "enabled", fallback=True) if config.has_section("OBS") else False
        if not desired_enabled:
            return (False, "OBS is disabled")

        try:
            await self._refresh_obs_integration_from_config()
        except Exception:
            pass

        obs = getattr(self.actions, 'obs', None)
        if obs is None or not getattr(self.actions, 'obs_integration_enabled', False):
            return (False, "OBS is not available")

        if not getattr(self.actions, 'chatdj_enabled', False) or not hasattr(self.actions, 'auto_dj'):
            return (False, "Spotify is not available")

        now_uri = getattr(self.actions.auto_dj, 'now_playing_track_uri', None)
        if not isinstance(now_uri, str) or now_uri.strip() == "":
            return (False, "No song is currently playing")

        try:
            enriched = await self._enrich_queue_tracks([now_uri])
            item = enriched[0] if isinstance(enriched, list) and enriched else None
        except Exception:
            item = None

        title = None
        artists_text = None
        album = None

        if isinstance(item, dict):
            v = item.get('name')
            if isinstance(v, str) and v.strip():
                title = v.strip()
            v = item.get('album')
            if isinstance(v, str) and v.strip():
                album = v.strip()
            v = item.get('artists')
            if isinstance(v, list):
                parts = [str(a).strip() for a in v if isinstance(a, str) and a.strip()]
                if parts:
                    artists_text = ", ".join(parts)

        if not title:
            title = "Unknown Title"
        if not artists_text:
            artists_text = "Unknown Artist"

        msg = f"{artists_text} - {title}"
        if album:
            msg = msg + f"\n{album}"

        duration = self._get_obs_overlay_duration_seconds()

        if not hasattr(obs, 'trigger_now_playing_overlay'):
            return (False, "OBS handler does not support now playing overlay")

        async def _run() -> None:
            try:
                try:
                    await obs.ensure_text_sources(scene_key='main', source_names=['NowPlayingOverlay'])
                except Exception:
                    pass
                await obs.trigger_now_playing_overlay(msg, duration)
            except Exception as exc:
                logger.exception(
                    "obs.now_playing_overlay.error",
                    message="Now playing overlay task failed",
                    exc=exc,
                )

        try:
            asyncio.create_task(_run())
        except Exception as exc:
            return (False, str(exc))

        return (True, None)

    async def _refresh_obs_integration_from_config(self) -> None:
        desired_enabled = config.getboolean("OBS", "enabled", fallback=True) if config.has_section("OBS") else False

        current_enabled = bool(getattr(self.actions, 'obs_integration_enabled', False))
        current_obs = getattr(self.actions, 'obs', None)

        if not desired_enabled:
            if current_enabled:
                try:
                    self.actions.obs_integration_enabled = False
                except Exception:
                    pass

                if current_obs is not None:
                    try:
                        await current_obs.disconnect()
                    except Exception:
                        pass
                    try:
                        delattr(self.actions, 'obs')
                    except Exception:
                        pass
            return

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

        recreate = False
        if not current_enabled or current_obs is None:
            recreate = True
        else:
            try:
                if getattr(current_obs, 'host', None) != host:
                    recreate = True
                if getattr(current_obs, 'port', None) != port:
                    recreate = True
                if getattr(current_obs, 'password', None) != password:
                    recreate = True
            except Exception:
                recreate = True

        if recreate:
            if current_obs is not None:
                try:
                    await current_obs.disconnect()
                except Exception:
                    pass
            try:
                from handlers.obshandler import OBSHandler
                self.actions.obs = OBSHandler(host=host, port=port, password=password)
            except Exception:
                return

        try:
            self.actions.obs_integration_enabled = True
        except Exception:
            pass

    async def start(self) -> None:
        self._tasks.append(asyncio.create_task(self._events_loop()))
        self._tasks.append(asyncio.create_task(self._tip_processor_loop()))
        self._tasks.append(asyncio.create_task(self._queue_watchdog()))
        self._tasks.append(asyncio.create_task(self._local_control_loop()))

        web_host = config.get("Web", "host", fallback="127.0.0.1") if config.has_section("Web") else "127.0.0.1"
        web_port = config.getint("Web", "port", fallback=8765) if config.has_section("Web") else 8765
        override_host, override_port = _get_web_runtime_overrides()
        if override_host is not None:
            web_host = override_host
        if override_port is not None:
            web_port = override_port
        try:
            self._web = WebUI(self, host=web_host, port=web_port)
            await self._web.start()
            logger.info("webui.started", message="Web UI started", data={"host": web_host, "port": web_port})
        except Exception as exc:
            logger.exception("webui.error", exc=exc, message="Failed to start Web UI")

    async def stop(self) -> None:
        self._stop_event.set()
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

        try:
            await self._stop_spotify_auth_server()
        except Exception:
            pass

        if self._web:
            try:
                await self._web.stop()
            except Exception:
                pass
            self._web = None

        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self.actions.auto_dj.check_queue_status, True)
            if getattr(self.actions.auto_dj, 'queued_tracks', []):
                await loop.run_in_executor(None, self.actions.auto_dj.clear_playback_context, True)
        except Exception:
            pass

    async def _queue_watchdog(self) -> None:
        if not getattr(self.actions, 'chatdj_enabled', False):
            return
        if not hasattr(self.actions, 'auto_dj'):
            return
        while not self._stop_event.is_set():
            try:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, self.actions.auto_dj.check_queue_status)
            except Exception as exc:
                logger.exception("song.queue.check.error", exc=exc, message="Queue watchdog error")

            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=5)
            except asyncio.TimeoutError:
                pass

    async def _local_control_loop(self) -> None:
        if not getattr(self.actions, 'chatdj_enabled', False):
            return
        if not hasattr(self.actions, 'auto_dj'):
            return

        logger.info(
            "local.control.ready",
            message="Local controls enabled. Type 'pause' or 'resume' in this console to pause/unpause the queue.",
        )

        buf = ""
        loop = asyncio.get_running_loop()
        is_windows = (os.name == 'nt')

        if is_windows:
            try:
                import msvcrt  # type: ignore
            except Exception:
                is_windows = False

        while not self._stop_event.is_set():
            try:
                if is_windows:
                    if msvcrt.kbhit():
                        ch = msvcrt.getwch()
                        if ch in ('\r', '\n'):
                            sys.stdout.write("\n")
                            sys.stdout.flush()
                            cmd = buf.strip().lower()
                            buf = ""
                            await self._handle_local_command(cmd, loop)
                        elif ch == '\x03':
                            shutdown_event.set()
                            break
                        elif ch == '\b':
                            buf = buf[:-1]
                            sys.stdout.write("\b \b")
                            sys.stdout.flush()
                        else:
                            buf += ch
                            sys.stdout.write(ch)
                            sys.stdout.flush()
                    else:
                        await asyncio.sleep(0.1)
                    continue

                line = await loop.run_in_executor(None, sys.stdin.readline)
                if line == "":
                    await asyncio.sleep(0.25)
                    continue
                cmd = line.strip().lower()
                await self._handle_local_command(cmd, loop)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.exception("local.control.error", exc=exc, message="Local control loop error")
                await asyncio.sleep(1)

    def _get_spotify_config_values(self) -> tuple[str, str, str]:
        if not config.has_section("Spotify"):
            return ("", "", "")
        client_id = config.get("Spotify", "client_id", fallback="").strip()
        client_secret = config.get("Spotify", "client_secret", fallback="").strip()
        redirect_url = config.get("Spotify", "redirect_url", fallback="").strip()
        return (client_id, client_secret, redirect_url)

    def _build_spotify_oauth(self):
        from spotipy import SpotifyOAuth

        client_id, client_secret, redirect_url = self._get_spotify_config_values()
        cache_path = get_spotipy_cache_path()
        ensure_parent_dir(cache_path)
        return SpotifyOAuth(
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_url,
            scope="user-modify-playback-state user-read-playback-state user-read-currently-playing user-read-private",
            open_browser=False,
            cache_path=str(cache_path),
        )

    def _is_spotify_authorized(self) -> bool:
        try:
            client_id, client_secret, redirect_url = self._get_spotify_config_values()
            if not client_id or not client_secret or not redirect_url:
                return False
            # NOTE: Avoid oauth.validate_token() here because it may attempt a token refresh
            # over the network. This method is used by the WebUI polling endpoint
            # (/api/spotify/auth/status) and must stay fast and non-blocking.
            oauth = self._build_spotify_oauth()
            token_info = oauth.cache_handler.get_cached_token()
            return bool(token_info)
        except Exception:
            return False

    async def get_spotify_auth_status(self) -> Dict[str, Any]:
        client_id, client_secret, redirect_url = self._get_spotify_config_values()
        configured = bool(client_id and client_secret and redirect_url)
        authorized = self._is_spotify_authorized()

        from helpers import spotify_client as helpers_spotify_client
        client_ready = helpers_spotify_client is not None

        async with self._spotify_auth_lock:
            return {
                "configured": configured,
                "authorized": authorized,
                "client_ready": client_ready,
                "redirect_url": redirect_url,
                "in_progress": bool(self._spotify_auth_in_progress),
                "auth_url": self._spotify_auth_url,
                "error": self._spotify_auth_error,
            }

    async def _stop_spotify_auth_server(self) -> None:
        runner: Optional[web.AppRunner] = None
        async with self._spotify_auth_lock:
            runner = self._spotify_auth_runner
            self._spotify_auth_runner = None
            self._spotify_auth_site = None
            self._spotify_auth_oauth = None
            self._spotify_auth_state = None
        if runner is not None:
            try:
                await runner.cleanup()
            except Exception:
                pass

    async def _try_enable_chatdj_from_current_config(self) -> bool:
        if getattr(self.actions, 'chatdj_enabled', False) and hasattr(self.actions, 'auto_dj'):
            return True

        from helpers import spotify_client as helpers_spotify_client
        if helpers_spotify_client is None:
            return False

        openai_api_key = config.get("OpenAI", "api_key", fallback="").strip()
        if openai_api_key in ("", "your-openai-api-key"):
            return False

        try:
            from chatdj import AutoDJ, SongExtractor

            google_api_key_raw = config.get("Search", "google_api_key", fallback="").strip()
            google_cx_raw = config.get("Search", "google_cx", fallback="").strip()
            google_api_key = google_api_key_raw if google_api_key_raw else None
            google_cx = google_cx_raw if google_cx_raw else None

            playback_device_id_raw = config.get("Spotify", "playback_device_id", fallback="").strip() if config.has_section("Spotify") else ""
            playback_device_id = playback_device_id_raw if playback_device_id_raw else None

            model = config.get("OpenAI", "model", fallback="gpt-5").strip() or "gpt-5"

            self.actions.song_extractor = SongExtractor(
                openai_api_key,
                spotify_client=helpers_spotify_client,
                google_api_key=google_api_key,
                google_cx=google_cx,
                model=model,
            )
            self.actions.auto_dj = AutoDJ(helpers_spotify_client, playback_device_id=playback_device_id)
            self.actions.chatdj_enabled = True
            return True
        except Exception:
            try:
                self.actions.chatdj_enabled = False
            except Exception:
                pass
            return False

    async def _spotify_auth_callback(self, request: web.Request) -> web.Response:
        err = request.query.get('error')
        state = request.query.get('state')
        code = request.query.get('code')

        expected_state: Optional[str]
        oauth: Any

        async with self._spotify_auth_lock:
            expected_state = self._spotify_auth_state
            oauth = self._spotify_auth_oauth

        if oauth is None:
            async with self._spotify_auth_lock:
                self._spotify_auth_in_progress = False
                self._spotify_auth_error = "Authorization session expired. Please try again."
            asyncio.create_task(self._stop_spotify_auth_server())
            return web.Response(text="Authorization session expired. You can close this window and retry.", content_type='text/plain', status=400)

        if isinstance(err, str) and err.strip() != "":
            async with self._spotify_auth_lock:
                self._spotify_auth_in_progress = False
                self._spotify_auth_error = f"Spotify auth error: {err.strip()}"
            asyncio.create_task(self._stop_spotify_auth_server())
            return web.Response(text="Spotify authorization failed. You can close this window.", content_type='text/plain')

        if not isinstance(code, str) or code.strip() == "":
            async with self._spotify_auth_lock:
                self._spotify_auth_in_progress = False
                self._spotify_auth_error = "Missing authorization code."
            asyncio.create_task(self._stop_spotify_auth_server())
            return web.Response(text="Missing authorization code. You can close this window.", content_type='text/plain', status=400)

        if expected_state is not None and state != expected_state:
            async with self._spotify_auth_lock:
                self._spotify_auth_in_progress = False
                self._spotify_auth_error = "State mismatch during authorization."
            asyncio.create_task(self._stop_spotify_auth_server())
            return web.Response(text="State mismatch. You can close this window.", content_type='text/plain', status=400)

        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, lambda: oauth.get_access_token(code=code, check_cache=False))
        except Exception as exc:
            async with self._spotify_auth_lock:
                self._spotify_auth_in_progress = False
                self._spotify_auth_error = str(exc)
            asyncio.create_task(self._stop_spotify_auth_server())
            return web.Response(text="Failed to complete Spotify authorization. You can close this window.", content_type='text/plain', status=500)

        try:
            from helpers import config as helpers_config
            from helpers import refresh_spotify_client
            helpers_config.read(config_path)
            refresh_spotify_client()
        except Exception:
            pass

        try:
            await self._try_enable_chatdj_from_current_config()
        except Exception:
            pass

        async with self._spotify_auth_lock:
            self._spotify_auth_in_progress = False
            self._spotify_auth_error = None

        asyncio.create_task(self._stop_spotify_auth_server())
        return web.Response(
            text=(
                "<html><head><meta charset=\"utf-8\" /><title>TipTune</title></head>"
                "<body style=\"font-family: sans-serif;\"><h2>Spotify connected.</h2>"
                "<div>You can close this window and return to TipTune.</div></body></html>"
            ),
            content_type='text/html',
            headers={"Cache-Control": "no-store"},
        )

    async def start_spotify_auth(self) -> tuple[bool, Optional[str], Optional[str]]:
        client_id, client_secret, redirect_url = self._get_spotify_config_values()
        if not client_id or not client_secret or not redirect_url:
            return (False, None, "Spotify client_id/client_secret/redirect_url must be configured first.")

        parsed = urlparse(redirect_url)
        host = (parsed.hostname or "").strip()
        port = parsed.port
        path = parsed.path or "/"
        if parsed.scheme != 'http':
            return (False, None, "Spotify redirect_url must be http://127.0.0.1:<port>/<path>.")
        if host not in ('127.0.0.1', 'localhost'):
            return (False, None, "Spotify redirect_url host must be 127.0.0.1 or localhost.")
        if port is None:
            return (False, None, "Spotify redirect_url must include an explicit port.")

        async with self._spotify_auth_lock:
            if self._spotify_auth_in_progress and self._spotify_auth_url:
                return (True, self._spotify_auth_url, None)

        await self._stop_spotify_auth_server()

        oauth = self._build_spotify_oauth()
        state = secrets.token_urlsafe(16)
        auth_url = oauth.get_authorize_url(state=state)

        async with self._spotify_auth_lock:
            self._spotify_auth_in_progress = True
            self._spotify_auth_error = None
            self._spotify_auth_url = auth_url
            self._spotify_auth_state = state
            self._spotify_auth_oauth = oauth

        callback_app = web.Application()
        cb_paths = {path}
        if path != '/':
            cb_paths.add(path.rstrip('/'))
            cb_paths.add(path.rstrip('/') + '/')
        for p in sorted(cb_paths):
            if p and p.startswith('/'):
                callback_app.router.add_get(p, self._spotify_auth_callback)

        runner = web.AppRunner(callback_app)
        await runner.setup()
        site = web.TCPSite(runner, host=host, port=int(port))
        try:
            await site.start()
        except Exception as exc:
            try:
                await runner.cleanup()
            except Exception:
                pass
            async with self._spotify_auth_lock:
                self._spotify_auth_in_progress = False
                self._spotify_auth_error = str(exc)
            return (False, None, f"Failed to start local callback server: {exc}")

        async with self._spotify_auth_lock:
            self._spotify_auth_runner = runner
            self._spotify_auth_site = site

        return (True, auth_url, None)

    async def _handle_local_command(self, cmd: str, loop: asyncio.AbstractEventLoop) -> None:
        if cmd in ("pause", "p"):
            await loop.run_in_executor(None, self.actions.auto_dj.pause_queue)
            return
        if cmd in ("resume", "unpause", "r"):
            await loop.run_in_executor(None, self.actions.auto_dj.unpause_queue)
            return
        if cmd in ("status", "s"):
            paused = await loop.run_in_executor(None, self.actions.auto_dj.queue_paused)
            queued = len(getattr(self.actions.auto_dj, 'queued_tracks', []))
            logger.info(
                "local.control.status",
                message="Queue status.",
                data={"paused": paused, "queued_tracks": queued}
            )
            return
        if cmd in ("help", "?"):
            logger.info(
                "local.control.help",
                message="Local commands: pause | resume | status | help"
            )
            return

    async def _events_loop(self) -> None:
        api: Optional[EventsAPIClient] = None
        api_url: Optional[str] = None
        api_rpm: Optional[int] = None

        async with httpx.AsyncClient() as client:
            while not self._stop_event.is_set():
                try:
                    events_api_url = config.get("Events API", "url", fallback="").strip()
                    max_rpm = config.getint("Events API", "max_requests_per_minute", fallback=1000)
                except Exception:
                    events_api_url = ""
                    max_rpm = 1000

                if not events_api_url:
                    api = None
                    api_url = None
                    api_rpm = None
                    try:
                        await asyncio.wait_for(self._stop_event.wait(), timeout=5)
                    except asyncio.TimeoutError:
                        pass
                    continue

                if api is None or api_url != events_api_url or api_rpm != max_rpm:
                    api = EventsAPIClient(events_api_url, max_requests_per_minute=max_rpm)
                    api_url = events_api_url
                    api_rpm = max_rpm

                try:
                    events = await api.poll(client)
                    for event in events:
                        self.publish_events_api_event(event)
                        await self._handle_event(event)
                except asyncio.CancelledError:
                    break
                except Exception as exc:
                    logger.exception("events_api.poll.error", exc=exc, message="Failed to poll Events API")

                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=api.poll_interval_seconds)
                except asyncio.TimeoutError:
                    pass

    async def _handle_event(self, event: Dict[str, Any]) -> None:
        if not isinstance(event, dict):
            return

        method = event.get('method')
        if method != 'tip':
            return

        tip_obj = event.get('object') if isinstance(event.get('object'), dict) else event
        await self._tip_queue.put(tip_obj)

    async def _tip_processor_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                tip_obj = await asyncio.wait_for(self._tip_queue.get(), timeout=1)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            try:
                await self._handle_tip(tip_obj)
            except Exception as exc:
                logger.exception("tip.queue.process.error", exc=exc, message="Error processing queued tip")
            finally:
                self._tip_queue.task_done()

    async def _handle_tip(self, event: Dict[str, Any]) -> None:
        try:
            tip_amount = event.get('tip', {}).get('tokens', 0)
            tip_message = event.get('tip', {}).get('message', '').strip()
            username = event.get('user', {}).get('username', 'Anonymous')

            if not isinstance(tip_amount, int) or tip_amount <= 0:
                return

            is_song_request = self.checks.is_song_request(tip_amount)
            is_skip_request = self.checks.is_skip_song_request(tip_amount)

            if not is_song_request and is_skip_request:
                skipped = await self.actions.skip_song()
                if not skipped:
                    await self.actions.trigger_warning_overlay(
                        username,
                        "Couldn't skip the current song.",
                        10
                    )
                return

            if not is_song_request:
                return

            request_count = max(1, self.checks.get_request_count(tip_amount))

            if tip_message == "":
                await self.actions.trigger_warning_overlay(
                    username,
                    "Couldn't identify a song in your tip, because the tip note was blank. It may have been removed due to blocked words.",
                    10
                )
                return

            if len(tip_message) < 3:
                tip_message = f"The song name might be \"{tip_message}\"."

            song_extracts = await self.actions.extract_song_titles(tip_message, request_count)

            if not song_extracts:
                song_extracts = [SongRequest(song=tip_message, artist="", spotify_uri=None)]

            for song_info in song_extracts:
                song_uri: Optional[str]
                if getattr(song_info, 'spotify_uri', None):
                    song_uri = song_info.spotify_uri
                else:
                    song_uri = await self.actions.find_song_spotify(song_info)

                if not song_uri:
                    await self.actions.trigger_warning_overlay(
                        username,
                        "Couldn't find song on Spotify. Did you include artist and song name?",
                        10
                    )
                    continue

                if not await self.actions.available_in_market(song_uri):
                    await self.actions.trigger_warning_overlay(
                        username,
                        "Requested song not available in US market.",
                        10
                    )
                    continue

                song_details = f"{song_info.artist} - {song_info.song}".strip()
                await self.actions.add_song_to_queue(song_uri, username, song_details)

        except Exception as exc:
            logger.exception("event.tip.error", exc=exc, message="Error processing tip event")

    def publish_events_api_event(self, event: Dict[str, Any]) -> None:
        if not isinstance(event, dict):
            return
        item = {
            "ts": time.time(),
            "event": event
        }

        self._events_recent.append(item)
        if len(self._events_recent) > self._events_recent_max:
            self._events_recent = self._events_recent[-self._events_recent_max:]

        for q in list(self._events_subscribers):
            try:
                q.put_nowait(item)
            except asyncio.QueueFull:
                try:
                    _ = q.get_nowait()
                except Exception:
                    pass
                try:
                    q.put_nowait(item)
                except Exception:
                    pass
            except Exception:
                pass

    def register_events_subscriber(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._events_subscribers.add(q)
        return q

    def unregister_events_subscriber(self, q: asyncio.Queue) -> None:
        try:
            self._events_subscribers.discard(q)
        except Exception:
            pass

    def get_recent_events(self, limit: int = 50) -> list[dict]:
        if limit <= 0:
            return []
        return self._events_recent[-limit:]

    async def get_queue_state(self) -> Dict[str, Any]:
        if not getattr(self.actions, 'chatdj_enabled', False):
            return {"enabled": False}
        if not hasattr(self.actions, 'auto_dj'):
            return {"enabled": False}

        try:
            queued_tracks = self.actions.auto_dj.get_queued_tracks_snapshot()
        except Exception:
            queued_tracks = list(getattr(self.actions.auto_dj, 'queued_tracks', []))
        playback_device_id = getattr(self.actions.auto_dj, 'playback_device', None)
        playback_device_name = getattr(self.actions.auto_dj, 'playback_device_name', None)

        paused = self.actions.auto_dj.queue_paused()

        playback_progress_ms: Optional[int] = None
        playback_is_playing: Optional[bool] = None
        playback_track_uri: Optional[str] = None

        now_playing_track = getattr(self.actions.auto_dj, 'now_playing_track_uri', None)
        now_playing_item: Optional[dict] = None
        if isinstance(now_playing_track, str) and now_playing_track.strip() != "":
            try:
                enriched_np = await self._enrich_queue_tracks([now_playing_track])
                if enriched_np and isinstance(enriched_np, list) and isinstance(enriched_np[0], dict):
                    now_playing_item = enriched_np[0]
                else:
                    now_playing_item = {"uri": now_playing_track}
            except Exception:
                now_playing_item = {"uri": now_playing_track}

            try:
                loop = asyncio.get_running_loop()
                pb = await loop.run_in_executor(None, self.actions.auto_dj.spotify.current_playback)
                if isinstance(pb, dict):
                    if pb.get('progress_ms') is not None:
                        playback_progress_ms = int(pb.get('progress_ms'))
                    if pb.get('is_playing') is not None:
                        playback_is_playing = bool(pb.get('is_playing'))
                    item = pb.get('item')
                    if isinstance(item, dict) and isinstance(item.get('uri'), str):
                        playback_track_uri = item.get('uri')
            except Exception:
                playback_progress_ms = None
                playback_is_playing = None
                playback_track_uri = None

            if isinstance(playback_track_uri, str) and playback_track_uri and playback_track_uri != now_playing_track:
                playback_progress_ms = None
                playback_is_playing = None
                playback_track_uri = None

        queued_items = await self._enrich_queue_tracks(queued_tracks)

        return {
            "enabled": True,
            "paused": bool(paused),
            "playback_progress_ms": playback_progress_ms,
            "playback_is_playing": playback_is_playing,
            "playback_track_uri": playback_track_uri,
            "now_playing_track": now_playing_track,
            "now_playing_item": now_playing_item,
            "queued_tracks": queued_tracks,
            "queued_items": queued_items,
            "playback_device_id": playback_device_id,
            "playback_device_name": playback_device_name,
        }

    def _parse_spotify_track_id(self, v: Any) -> Optional[str]:
        if not isinstance(v, str):
            return None
        s = v.strip()
        if s == "":
            return None
        prefix = "spotify:track:"
        if s.startswith(prefix):
            tid = s[len(prefix):].strip()
            return tid if tid else None
        marker = "open.spotify.com/track/"
        pos = s.find(marker)
        if pos >= 0:
            rest = s[pos + len(marker):]
            rest = rest.split('?', 1)[0]
            rest = rest.split('#', 1)[0]
            rest = rest.split('/', 1)[0]
            rest = rest.strip()
            return rest if rest else None
        return None

    def _cache_get_track(self, cache_key: str) -> Optional[Dict[str, Any]]:
        try:
            item = self._track_cache.get(cache_key)
            if not item:
                return None
            ts = float(item.get('ts', 0))
            if (time.time() - ts) > float(self._track_cache_ttl_seconds):
                try:
                    del self._track_cache[cache_key]
                except Exception:
                    pass
                return None
            meta = item.get('meta')
            return meta if isinstance(meta, dict) else None
        except Exception:
            return None

    def _cache_put_track(self, cache_key: str, meta: Dict[str, Any]) -> None:
        try:
            if not isinstance(cache_key, str) or cache_key.strip() == "":
                return
            if not isinstance(meta, dict):
                return
            self._track_cache[cache_key] = {"ts": time.time(), "meta": meta}
            if len(self._track_cache) > int(self._track_cache_max_items):
                items = list(self._track_cache.items())
                items.sort(key=lambda kv: float((kv[1] or {}).get('ts', 0)))
                trim = max(0, len(items) - int(self._track_cache_max_items))
                for i in range(trim):
                    try:
                        del self._track_cache[items[i][0]]
                    except Exception:
                        pass
        except Exception:
            return

    async def _fetch_spotify_track_meta(self, track_uri: str) -> Optional[Dict[str, Any]]:
        if not isinstance(track_uri, str) or track_uri.strip() == "":
            return None
        if not getattr(self.actions, 'chatdj_enabled', False):
            return None
        if not hasattr(self.actions, 'auto_dj'):
            return None
        spotify = getattr(self.actions.auto_dj, 'spotify', None)
        if spotify is None:
            return None

        loop = asyncio.get_running_loop()
        try:
            data = await asyncio.wait_for(loop.run_in_executor(None, spotify.track, track_uri), timeout=4)
        except asyncio.TimeoutError:
            return None
        except Exception:
            return None

        if not isinstance(data, dict):
            return None

        name = data.get('name')
        artists_raw = data.get('artists')
        artists: list[str] = []
        if isinstance(artists_raw, list):
            for a in artists_raw:
                if isinstance(a, dict):
                    an = a.get('name')
                    if isinstance(an, str) and an.strip() != "":
                        artists.append(an)

        album_name = None
        album_image_url = None
        album_raw = data.get('album')
        if isinstance(album_raw, dict):
            an = album_raw.get('name')
            if isinstance(an, str) and an.strip() != "":
                album_name = an
            imgs = album_raw.get('images')
            if isinstance(imgs, list) and imgs:
                first = imgs[0]
                if isinstance(first, dict):
                    u = first.get('url')
                    if isinstance(u, str) and u.strip() != "":
                        album_image_url = u

        duration_ms = data.get('duration_ms')
        explicit = bool(data.get('explicit', False))
        preview_url = data.get('preview_url') if isinstance(data.get('preview_url'), str) else None
        external_urls = data.get('external_urls')
        spotify_url = None
        if isinstance(external_urls, dict):
            u = external_urls.get('spotify')
            if isinstance(u, str) and u.strip() != "":
                spotify_url = u

        track_id = data.get('id') if isinstance(data.get('id'), str) else None

        out: Dict[str, Any] = {
            "track_id": track_id,
            "name": name,
            "artists": artists,
            "album": album_name,
            "duration_ms": duration_ms,
            "explicit": explicit,
            "spotify_url": spotify_url,
            "preview_url": preview_url,
            "album_image_url": album_image_url,
        }

        clean: Dict[str, Any] = {}
        for k, v in out.items():
            if v is None:
                continue
            clean[k] = v
        return clean

    async def _enrich_queue_tracks(self, queued_tracks: list[Any]) -> list[dict]:
        tracks = queued_tracks if isinstance(queued_tracks, list) else []
        items: list[dict] = []

        to_fetch: list[tuple[int, str, str]] = []
        max_fetch = 10

        for idx, raw in enumerate(tracks):
            uri = raw if isinstance(raw, str) else str(raw)
            tid = self._parse_spotify_track_id(uri)
            cache_key = tid or uri
            meta = self._cache_get_track(cache_key)

            item: Dict[str, Any] = {
                "uri": uri,
            }
            if tid:
                item["track_id"] = tid
            if meta:
                item.update(meta)
            else:
                if len(to_fetch) < max_fetch:
                    to_fetch.append((idx, cache_key, uri))

            items.append(item)

        if not to_fetch:
            return items

        tasks = [self._fetch_spotify_track_meta(uri) for (_idx, _key, uri) in to_fetch]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for i, res in enumerate(results):
            if isinstance(res, dict):
                idx, cache_key, _uri = to_fetch[i]
                self._cache_put_track(cache_key, res)
                try:
                    items[idx].update(res)
                except Exception:
                    pass

        return items

    async def pause_queue(self) -> bool:
        if not getattr(self.actions, 'chatdj_enabled', False):
            return False
        if not hasattr(self.actions, 'auto_dj'):
            return False
        ok = bool(self.actions.auto_dj.pause_queue())
        if ok:
            try:
                await self.actions.trigger_queue_state_overlay(
                    "Song queue paused — current song will finish, then new requests will wait."
                )
            except Exception:
                pass
        return ok

    async def resume_queue(self) -> bool:
        if not getattr(self.actions, 'chatdj_enabled', False):
            return False
        if not hasattr(self.actions, 'auto_dj'):
            return False
        ok = bool(self.actions.auto_dj.unpause_queue())
        if ok:
            try:
                await self.actions.trigger_queue_state_overlay("Song request queue resumed")
            except Exception:
                pass
        return ok

    async def move_queue_item(self, from_index: int, to_index: int) -> bool:
        if not getattr(self.actions, 'chatdj_enabled', False):
            return False
        if not hasattr(self.actions, 'auto_dj'):
            return False

        loop = asyncio.get_running_loop()
        try:
            ok = await asyncio.wait_for(
                loop.run_in_executor(None, self.actions.auto_dj.move_queued_track, from_index, to_index),
                timeout=2,
            )
        except asyncio.TimeoutError:
            return False
        return bool(ok)

    def _normalize_spotify_track_uri(self, v: Any) -> Optional[str]:
        if not isinstance(v, str):
            return None
        s = v.strip()
        if s == "":
            return None
        if s.startswith('spotify:track:'):
            return s

        tid = self._parse_spotify_track_id(s)
        if tid:
            return f"spotify:track:{tid}"

        if s.isalnum() and 10 <= len(s) <= 64:
            return f"spotify:track:{s}"

        return s

    async def add_track_to_queue(self, uri: Any) -> bool:
        if not getattr(self.actions, 'chatdj_enabled', False):
            return False
        if not hasattr(self.actions, 'auto_dj'):
            return False

        track_uri = self._normalize_spotify_track_uri(uri)
        if not track_uri:
            return False

        loop = asyncio.get_running_loop()
        try:
            ok = await asyncio.wait_for(
                loop.run_in_executor(None, self.actions.auto_dj.add_song_to_queue, track_uri, True),
                timeout=2,
            )
        except asyncio.TimeoutError:
            return False
        except Exception:
            return False
        return bool(ok)

    async def insert_track_to_queue(self, uri: Any, index: int = 0) -> bool:
        if not getattr(self.actions, 'chatdj_enabled', False):
            return False
        if not hasattr(self.actions, 'auto_dj'):
            return False

        track_uri = self._normalize_spotify_track_uri(uri)
        if not track_uri:
            return False

        loop = asyncio.get_running_loop()
        try:
            ok = await asyncio.wait_for(
                loop.run_in_executor(None, self.actions.auto_dj.insert_song_to_queue, track_uri, int(index), True),
                timeout=2,
            )
        except asyncio.TimeoutError:
            return False
        except Exception:
            return False
        return bool(ok)

    async def search_spotify_tracks(self, query: str, limit: int = 10) -> list[dict]:
        loop = asyncio.get_running_loop()

        from helpers import refresh_spotify_client
        from helpers import spotify_client as helpers_spotify_client

        if helpers_spotify_client is None:
            try:
                await asyncio.wait_for(
                    loop.run_in_executor(None, refresh_spotify_client),
                    timeout=5,
                )
            except asyncio.TimeoutError:
                raise RuntimeError("Timed out initializing Spotify client")

            from helpers import spotify_client as helpers_spotify_client2
            helpers_spotify_client = helpers_spotify_client2

        if helpers_spotify_client is None:
            raise RuntimeError("Spotify client not ready")

        q = query.strip() if isinstance(query, str) else ''
        if q == '' or len(q) < 2:
            return []

        try:
            def _do_search():
                return helpers_spotify_client.search(q=q, type='track', market='US', limit=int(limit))

            payload = await asyncio.wait_for(
                loop.run_in_executor(None, _do_search),
                timeout=6,
            )
        except asyncio.TimeoutError:
            raise RuntimeError("Timed out searching Spotify")
        except Exception as exc:
            raise RuntimeError(str(exc))

        items = []
        if isinstance(payload, dict):
            tracks = payload.get('tracks')
            if isinstance(tracks, dict):
                raw_items = tracks.get('items', [])
                if isinstance(raw_items, list):
                    items = raw_items

        out: list[dict] = []
        for t in items:
            if not isinstance(t, dict):
                continue

            track_id = t.get('id') if isinstance(t.get('id'), str) else None
            uri = t.get('uri') if isinstance(t.get('uri'), str) else None
            name = t.get('name') if isinstance(t.get('name'), str) else None

            artists: list[str] = []
            artists_raw = t.get('artists')
            if isinstance(artists_raw, list):
                for a in artists_raw:
                    if isinstance(a, dict):
                        an = a.get('name')
                        if isinstance(an, str) and an.strip() != '':
                            artists.append(an)

            album_name = None
            album_image_url = None
            album_raw = t.get('album')
            if isinstance(album_raw, dict):
                an = album_raw.get('name')
                if isinstance(an, str) and an.strip() != '':
                    album_name = an
                imgs = album_raw.get('images')
                if isinstance(imgs, list) and imgs:
                    first = imgs[0]
                    if isinstance(first, dict):
                        u = first.get('url')
                        if isinstance(u, str) and u.strip() != '':
                            album_image_url = u

            duration_ms = t.get('duration_ms')
            explicit = bool(t.get('explicit', False))
            external_urls = t.get('external_urls')
            spotify_url = None
            if isinstance(external_urls, dict):
                u = external_urls.get('spotify')
                if isinstance(u, str) and u.strip() != '':
                    spotify_url = u

            item: Dict[str, Any] = {}
            if uri is not None:
                item['uri'] = uri
            if track_id is not None:
                item['track_id'] = track_id
            if name is not None:
                item['name'] = name
            if artists:
                item['artists'] = artists
            if album_name is not None:
                item['album'] = album_name
            if duration_ms is not None:
                item['duration_ms'] = duration_ms
            if explicit is not None:
                item['explicit'] = explicit
            if spotify_url is not None:
                item['spotify_url'] = spotify_url
            if album_image_url is not None:
                item['album_image_url'] = album_image_url

            if item:
                out.append(item)
        return out

    async def delete_queue_item(self, index: int) -> bool:
        if not getattr(self.actions, 'chatdj_enabled', False):
            return False
        if not hasattr(self.actions, 'auto_dj'):
            return False

        loop = asyncio.get_running_loop()
        try:
            ok = await asyncio.wait_for(
                loop.run_in_executor(None, self.actions.auto_dj.delete_queued_track, index),
                timeout=2,
            )
        except asyncio.TimeoutError:
            return False
        return bool(ok)

    async def get_devices(self) -> list[dict]:
        if not getattr(self.actions, 'chatdj_enabled', False):
            return []
        if not hasattr(self.actions, 'auto_dj'):
            return []
        loop = asyncio.get_running_loop()
        try:
            devices = await asyncio.wait_for(
                loop.run_in_executor(None, self.actions.auto_dj.get_available_devices),
                timeout=5,
            )
        except asyncio.TimeoutError:
            return []
        return devices if isinstance(devices, list) else []

    async def get_spotify_devices(self) -> tuple[list[dict], Optional[str]]:
        loop = asyncio.get_running_loop()

        from helpers import refresh_spotify_client
        from helpers import spotify_client as helpers_spotify_client

        if helpers_spotify_client is None:
            try:
                await asyncio.wait_for(
                    loop.run_in_executor(None, refresh_spotify_client),
                    timeout=5,
                )
            except asyncio.TimeoutError:
                return ([], "Timed out initializing Spotify client")

            from helpers import spotify_client as helpers_spotify_client2
            helpers_spotify_client = helpers_spotify_client2

        if helpers_spotify_client is None:
            return ([], "Spotify client not ready")

        try:
            payload = await asyncio.wait_for(
                loop.run_in_executor(None, helpers_spotify_client.devices),
                timeout=5,
            )
        except asyncio.TimeoutError:
            return ([], "Timed out listing devices")
        except Exception as exc:
            logger.exception("spotify.devices.error", exc=exc, message="Failed to list Spotify devices")
            return ([], str(exc))

        devices = payload.get('devices', []) if isinstance(payload, dict) else []
        if isinstance(devices, list):
            return (devices, None)
        return ([], None)

    async def set_device(self, device_id: Any, persist: bool = True) -> bool:
        if not getattr(self.actions, 'chatdj_enabled', False):
            return False
        if not hasattr(self.actions, 'auto_dj'):
            return False
        if not isinstance(device_id, str) or device_id.strip() == '':
            return False

        loop = asyncio.get_running_loop()
        try:
            ok = await asyncio.wait_for(
                loop.run_in_executor(None, self.actions.auto_dj.set_playback_device, device_id, False, False),
                timeout=10,
            )
        except asyncio.TimeoutError:
            return False
        if not ok:
            return False

        if persist:
            await self.update_config_from_ui({"Spotify": {"playback_device_id": device_id}})
        return True

    async def set_spotify_device(self, device_id: Any, persist: bool = True) -> bool:
        if not isinstance(device_id, str) or device_id.strip() == '':
            return False
        device_id = device_id.strip()

        if getattr(self.actions, 'chatdj_enabled', False) and hasattr(self.actions, 'auto_dj'):
            return await self.set_device(device_id, persist=persist)

        loop = asyncio.get_running_loop()

        from helpers import refresh_spotify_client
        from helpers import spotify_client as helpers_spotify_client

        if helpers_spotify_client is None:
            try:
                await asyncio.wait_for(
                    loop.run_in_executor(None, refresh_spotify_client),
                    timeout=5,
                )
            except asyncio.TimeoutError:
                return False

            from helpers import spotify_client as helpers_spotify_client2
            helpers_spotify_client = helpers_spotify_client2

        if helpers_spotify_client is None:
            return False

        try:
            ok = await asyncio.wait_for(
                loop.run_in_executor(None, helpers_spotify_client.transfer_playback, device_id, False),
                timeout=10,
            )
        except asyncio.TimeoutError:
            return False
        except Exception:
            return False

        if not ok:
            return False

        if persist:
            await self.update_config_from_ui({"Spotify": {"playback_device_id": device_id}})
        return True

    def get_config_for_ui(self) -> Dict[str, Dict[str, str]]:
        cfg: Dict[str, Dict[str, str]] = {}
        for section in ("Events API", "OpenAI", "Spotify", "Search", "General", "OBS", "Web"):
            if not config.has_section(section):
                continue
            cfg[section] = {}
            for key, val in config.items(section):
                if _is_secret_field(section, key):
                    cfg[section][key] = ""
                else:
                    cfg[section][key] = val
        return cfg

    async def update_config_from_ui(self, payload: Any) -> tuple[bool, Optional[str]]:
        if not isinstance(payload, dict):
            return (False, "Invalid JSON")

        allowed: Dict[str, set[str]] = {
            "Events API": {"url", "max_requests_per_minute"},
            "OpenAI": {"api_key", "model"},
            "Spotify": {"client_id", "client_secret", "redirect_url", "playback_device_id"},
            "Search": {"google_api_key", "google_cx"},
            "General": {"song_cost", "skip_song_cost", "request_overlay_duration", "setup_complete"},
            "OBS": {"enabled", "host", "port", "password"},
            "Web": {"host", "port"},
        }

        updates: Dict[str, Dict[str, str]] = {}
        for section, options in payload.items():
            if section not in allowed:
                continue
            if not isinstance(options, dict):
                continue
            for key, value in options.items():
                if key not in allowed[section]:
                    continue
                if value is None:
                    continue

                value_str = str(value)
                if _is_secret_field(section, key) and value_str.strip() == "":
                    continue

                updates.setdefault(section, {})[key] = value_str

        if not updates:
            return (True, None)

        try:
            _update_ini_file(config_path, updates)
        except Exception as exc:
            return (False, str(exc))

        try:
            config.read(config_path)
        except Exception:
            pass

        try:
            from helpers import config as helpers_config
            from helpers import refresh_spotify_client
            helpers_config.read(config_path)
            refresh_spotify_client()
        except Exception:
            pass

        try:
            self.checks = Checks()
        except Exception:
            pass

        try:
            if getattr(self.actions, 'chatdj_enabled', False):
                from chatdj import SongExtractor
                from helpers import spotify_client

                google_api_key = config.get("Search", "google_api_key", fallback=None) if config.has_section("Search") else None
                google_cx = config.get("Search", "google_cx", fallback=None) if config.has_section("Search") else None

                openai_api_key = config.get("OpenAI", "api_key", fallback="").strip()
                if openai_api_key:
                    self.actions.song_extractor = SongExtractor(
                        openai_api_key,
                        spotify_client=spotify_client,
                        google_api_key=google_api_key,
                        google_cx=google_cx,
                        model=config.get("OpenAI", "model", fallback="gpt-5")
                    )
                self.actions.request_overlay_duration = config.getint("General", "request_overlay_duration", fallback=10)
        except Exception:
            pass

        try:
            await self._refresh_obs_integration_from_config()
        except Exception:
            pass

        return (True, None)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    loop = asyncio.get_event_loop()
    loop.set_exception_handler(handle_exception)

    asyncio.create_task(_watch_parent_process())

    service = SongRequestService()
    await service.start()

    signals = (signal.SIGTERM, signal.SIGINT)
    for s in signals:
        try:
            loop.add_signal_handler(s, lambda s=s: shutdown_event.set())
        except NotImplementedError:
            pass

    await shutdown_event.wait()
    await service.stop()


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
