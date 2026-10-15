import asyncio
from urllib.parse import urlsplit
from typing import Any, Dict, Optional

import simpleobsws
import yaml

from utils.runtime_paths import get_resource_path
from utils.structured_logging import get_structured_logger

logger = get_structured_logger('tiptune.handlers.obshandler')

REQUIRED_TEXT_SOURCES = ['SongRequester', 'WarningOverlay', 'GeneralOverlay', 'NowPlayingOverlay']
CABLE_DEVICE = 'CABLE Output (VB-Audio Virtual Cable)'
CABLE_PLACEHOLDER = 'tiptune-vb-cable-unconfigured'
CABLE_SOURCE_NAMES = ['TipTune Audio', 'TipTune Audio 2', 'TipTune Audio 3', 'TipTune Audio Capture']

DEFAULT_TEXT_SETTINGS_GDIPLUS: Dict[str, Any] = {
    'align': 'left',
    'chatlog': False,
    'extents': False,
    'font': {
        'face': 'Cascadia Mono',
        'flags': 0,
        'size': 256,
        'style': 'Regular',
    },
    'outline': True,
    'outline_color': 4278190080,
    'outline_size': 20,
    'text': '',
}

def _audio_window_executable(value: Any) -> Optional[str]:
    # OBS stores window selectors as escaped-title:window-class:executable.
    # A title or unrelated property mentioning an EXE is not a capture target.
    if not isinstance(value, str):
        return None
    parts = value.rsplit(':', 2)
    return parts[2].casefold() if len(parts) == 3 and parts[2] else None


def _first_string(*vals: Any) -> Optional[str]:
    for v in vals:
        if isinstance(v, str) and v.strip():
            return v
    return None

class OBSHandler:
    def __init__(self, host: str = 'localhost', port: int = 4455, password: Optional[str] = None):
        """Initialize OBS WebSocket handler.

        Args:
            host: OBS WebSocket server host
            port: OBS WebSocket server port
            password: OBS WebSocket server password
        """
        self.host = host
        self.port = port
        self.password = password
        self.ws: Optional[simpleobsws.WebSocketClient] = None
        self._connected = False

        # Get the current event loop or create a new one
        try:
            self.loop = asyncio.get_event_loop()
            logger.debug("obs.init.loop",
                        message="Using existing event loop",
                        data={"running": self.loop.is_running()})
        except RuntimeError:
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            logger.debug("obs.init.loop",
                        message="Created new event loop",
                        data={"loop_type": "new"})

        # Load scene definitions
        self.scenes = self._load_scenes()
        if self.scenes:
            logger.info("obs.scenes.load",
                       message="Successfully loaded scene definitions",
                       data={"scene_count": len(self.scenes)})
        else:
            logger.warning("obs.scenes.error",
                         message="Failed to load scene definitions")

    def _load_scenes(self) -> Optional[Dict]:
        """Load scene definitions from YAML file."""
        try:
            scenes_file = get_resource_path('scenes.yaml')
            with open(scenes_file, 'r') as f:
                config = yaml.safe_load(f)
                logger.debug("obs.scenes.load",
                           message="Loaded scene definitions",
                           data={"file": str(scenes_file)})
                return config.get('scenes', {})
        except Exception as exc:
            logger.exception("obs.scenes.error",
                           message="Failed to load scene definitions",
                           exc=exc,
                           data={"file": str(scenes_file)})
            return None

    async def _send_request_with_status(self, request_type: str, request_data: Optional[Dict] = None, max_retries: int = 2) -> tuple[bool, Optional[Dict[str, Any]], Optional[int], Optional[str]]:
        retries = 0

        while retries <= max_retries:
            try:
                if not self.ws or not self._connected:
                    if retries < max_retries:
                        if await self._try_reconnect():
                            logger.info("obs.reconnect.success",
                                      message="Reconnected successfully")
                        else:
                            logger.error("obs.reconnect.error",
                                       message="Reconnection failed")
                            retries += 1
                            await asyncio.sleep(1)
                            continue
                    else:
                        logger.error("obs.request.error",
                                   message="Not connected and max retries exceeded",
                                   data={"request_type": request_type})
                        return (False, None, None, "not_connected")

                request = simpleobsws.Request(request_type, request_data)
                response = await self.ws.call(request)

                if response and response.ok():
                    return (True, response.responseData, None, None)

                code: Optional[int] = None
                comment: Optional[str] = None
                status = getattr(response, 'requestStatus', None) if response is not None else None
                if isinstance(status, dict):
                    try:
                        code_raw = status.get('code')
                        code = int(code_raw) if code_raw is not None else None
                    except Exception:
                        code = None
                    try:
                        comment_raw = status.get('comment')
                        comment = str(comment_raw) if comment_raw is not None else None
                    except Exception:
                        comment = None
                else:
                    try:
                        code_raw = getattr(status, 'code', None)
                        code = int(code_raw) if code_raw is not None else None
                    except Exception:
                        code = None
                    try:
                        comment_raw = getattr(status, 'comment', None)
                        comment = str(comment_raw) if comment_raw is not None else None
                    except Exception:
                        comment = None

                if retries < max_retries:
                    retries += 1
                    await asyncio.sleep(1)
                    continue

                return (False, None, code, comment or "request_failed")

            except simpleobsws.NotIdentifiedError:
                logger.warning("obs.connection.lost",
                             message="Lost connection to OBS WebSocket")
                self._connected = False
                if retries < max_retries:
                    if await self._try_reconnect():
                        logger.info("obs.reconnect.success",
                                  message="Reconnected successfully")
                        retries += 1
                        continue
                    else:
                        logger.error("obs.reconnect.error",
                                   message="Reconnection failed")
                        retries += 1
                        await asyncio.sleep(1)
                        continue
                return (False, None, None, "not_identified")
            except Exception as exc:
                logger.exception("obs.request.error",
                               exc=exc,
                               message="Failed to send request",
                               data={"request_type": request_type})
                if retries < max_retries:
                    retries += 1
                    await asyncio.sleep(1)
                    continue
                return (False, None, None, str(exc))

        return (False, None, None, "request_failed")

    def _get_scene_name(self, scene_key: str) -> Optional[str]:
        """Get the actual scene name from scene key."""
        if not self.scenes or scene_key not in self.scenes:
            logger.warning("obs.scene.notfound",
                         message="Scene key not found",
                         data={
                             "scene_key": scene_key,
                             "available_scenes": list(self.scenes.keys()) if self.scenes else []
                         })
            return None
        return self.scenes[scene_key]['name']

    def _resolve_scene_name(self, scene_name: Optional[str] = None, scene_key: Optional[str] = None) -> Optional[str]:
        if isinstance(scene_name, str) and scene_name.strip():
            return scene_name.strip()
        if isinstance(scene_key, str) and scene_key.strip():
            return self._get_scene_name(scene_key.strip())
        return None

    async def list_scene_names(self) -> Optional[list[str]]:
        resp = await self.send_request('GetSceneList')
        if not isinstance(resp, dict):
            return None
        scenes = resp.get('scenes')
        if not isinstance(scenes, list):
            return None
        out: list[str] = []
        for s in scenes:
            if not isinstance(s, dict):
                continue
            name = s.get('sceneName')
            if isinstance(name, str) and name.strip():
                out.append(name.strip())
        return out

    async def connect(self) -> bool:
        """Connect to OBS WebSocket server."""
        try:
            ws_url = f"ws://{self.host}:{self.port}"
            logger.info("obs.connect.start",
                       message="Starting OBS WebSocket connection",
                       data={
                           "url": ws_url,
                           "password_set": bool(self.password),
                           "host": self.host,
                           "port": self.port
                       })

            ws_params = simpleobsws.IdentificationParameters(ignoreNonFatalRequestChecks=False)
            self.ws = simpleobsws.WebSocketClient(
                url=ws_url,
                password=self.password,
                identification_parameters=ws_params
            )

            try:
                logger.info("obs.connect.step", message="Initiating WebSocket connection")
                await self.ws.connect()
                logger.info("obs.connect.step", message="WebSocket connected, waiting for identification")
                await self.ws.wait_until_identified()
                logger.info("obs.connect.step", message="WebSocket identified successfully")

                self._connected = True
                logger.info("obs.connect.success",
                           message="Connected to OBS WebSocket",
                           data={"url": ws_url})
                return True

            except asyncio.TimeoutError:
                logger.error("obs.connect.timeout",
                           message="Connection attempt timed out",
                           data={"url": ws_url})
                self._connected = False
                return False
            except ConnectionRefusedError:
                logger.error("obs.connect.refused",
                           message="Connection refused - is OBS running and WebSocket server enabled?",
                           data={"url": ws_url})
                self._connected = False
                return False
            except Exception as exc:
                logger.error("obs.connect.error",
                           message="WebSocket connection failed",
                           data={"error": str(exc), "url": ws_url})
                self._connected = False
                return False

        except Exception as exc:
            logger.exception("obs.connect.error",
                           exc=exc,
                           message="Failed to connect to OBS WebSocket",
                           data={"url": ws_url})
            self._connected = False
            return False

    async def disconnect(self) -> None:
        """Disconnect from OBS WebSocket server."""
        if self.ws and self._connected:
            await self.ws.disconnect()
            self._connected = False
            logger.info("obs.disconnect",
                       message="Disconnected from OBS WebSocket")

    async def _try_reconnect(self) -> bool:
        """Attempt to reconnect to OBS WebSocket server."""
        logger.info("obs.reconnect",
                   message="Attempting to reconnect to OBS WebSocket")
        return await self.connect()

    async def send_request(self, request_type: str, request_data: Optional[Dict] = None, max_retries: int = 2) -> Optional[Dict[str, Any]]:
        """Send a request to OBS WebSocket server with retry logic.

        Args:
            request_type: Type of request to send
            request_data: Data to send with request
            max_retries: Maximum number of retry attempts (default: 2)

        Returns:
            Response from OBS WebSocket server or None if request failed
        """
        retries = 0

        while retries <= max_retries:
            try:
                if not self.ws or not self._connected:
                    if retries < max_retries:
                        if await self._try_reconnect():
                            logger.info("obs.reconnect.success",
                                      message="Reconnected successfully")
                        else:
                            logger.error("obs.reconnect.error",
                                       message="Reconnection failed")
                            retries += 1
                            await asyncio.sleep(1)  # Wait before retry
                            continue
                    else:
                        logger.error("obs.request.error",
                                   message="Not connected and max retries exceeded",
                                   data={"request_type": request_type})
                        return None

                request = simpleobsws.Request(request_type, request_data)
                response = await self.ws.call(request)

                if response and response.ok():
                    logger.debug("obs.request.success",
                               message="Request successful",
                               data={
                                   "type": request_type,
                                   "data": request_data
                               })
                    return response.responseData
                else:
                    logger.error("obs.request.failed",
                               message="Request failed",
                               data={
                                   "type": request_type,
                                   "retry": retries + 1,
                                   "max_retries": max_retries
                               })
                    if retries < max_retries:
                        retries += 1
                        await asyncio.sleep(1)  # Wait before retry
                        continue
                    return None

            except simpleobsws.NotIdentifiedError:
                logger.warning("obs.connection.lost",
                             message="Lost connection to OBS WebSocket")
                self._connected = False
                if retries < max_retries:
                    if await self._try_reconnect():
                        logger.info("obs.reconnect.success",
                                  message="Reconnected successfully")
                        retries += 1
                        continue
                    else:
                        logger.error("obs.reconnect.error",
                                   message="Reconnection failed")
                        retries += 1
                        await asyncio.sleep(1)  # Wait before retry
                        continue
                return None
            except Exception as exc:
                logger.exception("obs.request.error",
                               exc=exc,
                               message="Failed to send request",
                               data={"request_type": request_type})
                if retries < max_retries:
                    retries += 1
                    await asyncio.sleep(1)  # Wait before retry
                    continue
                return None

        return None

    # Convenience methods for common OBS operations
    async def set_scene(self, scene_key: str) -> bool:
        """Switch to the specified scene.

        Args:
            scene_key: Key of the scene in the YAML config

        Returns:
            True if successful, False otherwise
        """
        scene_name = self._get_scene_name(scene_key)
        if not scene_name:
            return False

        logger.debug("obs.scene.set",
                    message="Setting current scene",
                    data={
                        "scene_key": scene_key,
                        "scene_name": scene_name
                    })

        response = await self.send_request('SetCurrentProgramScene', {'sceneName': scene_name})
        success = response is not None

        if success:
            logger.info("obs.scene.set.success",
                       message="Set current scene",
                       data={"scene_name": scene_name})
        else:
            logger.error("obs.scene.set.error",
                        message="Failed to set scene",
                        data={"scene_name": scene_name})

        return success

    async def get_current_scene(self) -> Optional[str]:
        """Get the name of the current scene.

        Returns:
            Name of current scene or None if request failed
        """
        response = await self.send_request('GetCurrentProgramScene')
        if response:
            scene_name = response.get('currentProgramSceneName')
            logger.debug("obs.scene.get",
                        message="Retrieved current scene",
                        data={"scene": scene_name})
            return scene_name
        return None

    async def _get_input_names(self) -> Optional[set[str]]:
        response = await self.send_request('GetInputList')
        if not response:
            return None
        inputs = response.get('inputs')
        if not isinstance(inputs, list):
            return None
        names: set[str] = set()
        for item in inputs:
            if not isinstance(item, dict):
                continue
            name = item.get('inputName')
            if isinstance(name, str) and name.strip():
                names.add(name)
        return names

    async def _get_inputs(self) -> Optional[list[Dict[str, Any]]]:
        response = await self.send_request('GetInputList')
        if not response:
            return None
        inputs = response.get('inputs')
        if not isinstance(inputs, list):
            return None
        out: list[Dict[str, Any]] = []
        for item in inputs:
            if isinstance(item, dict):
                out.append(item)
        return out

    async def _get_scene_item_id_by_scene_name(self, scene_name: str, source_name: str) -> Optional[int]:
        if not scene_name or not source_name:
            return None
        id_response = await self.send_request('GetSceneItemId', {
            'sceneName': scene_name,
            'sourceName': source_name
        })
        if not id_response or 'sceneItemId' not in id_response:
            return None
        try:
            return int(id_response['sceneItemId'])
        except Exception:
            return None

    async def get_text_source_status(self, scene_key: str = 'main', source_names: Optional[list[str]] = None, scene_name: Optional[str] = None) -> Optional[Dict[str, Any]]:
        names = source_names or list(REQUIRED_TEXT_SOURCES)

        current_scene = await self.get_current_scene()
        main_scene = self._resolve_scene_name(scene_name=scene_name, scene_key=scene_key)

        input_names = await self._get_input_names()
        if input_names is None:
            return None

        sources = []
        for n in names:
            input_exists = n in input_names
            in_main_scene = False
            if main_scene and input_exists:
                in_main_scene = (await self._get_scene_item_id_by_scene_name(main_scene, n)) is not None
            sources.append({
                'name': n,
                'input_exists': bool(input_exists),
                'in_main_scene': bool(in_main_scene),
                'present': bool(input_exists and in_main_scene),
            })

        return {
            'current_scene': current_scene,
            'main_scene': main_scene,
            'sources': sources,
        }

    @staticmethod
    def _is_overlay_url(url: str) -> bool:
        try:
            parsed = urlsplit(url)
            return (parsed.scheme == 'http' and parsed.hostname in ('127.0.0.1', 'localhost', '::1')
                    and parsed.path == '/overlay' and not parsed.username and not parsed.password)
        except (ValueError, TypeError):
            return False

    async def get_browser_source_status(self, scene: str, name: str, url: str) -> Dict[str, Any]:
        inputs = await self._get_inputs()
        source = next((item for item in inputs or [] if item.get('inputName') == name), None)
        result = {'name': name, 'input_exists': source is not None, 'in_scene': False,
                  'configured': False, 'width': 1920, 'height': 1080, 'error': None}
        if not source:
            return result
        if source.get('inputKind') != 'browser_source':
            return dict(result, error='This source name belongs to another input kind. Choose a different name.')
        ok, data, _, error = await self._send_request_with_status('GetInputSettings', {'inputName': name})
        if not ok:
            return dict(result, error=error)
        settings = (data or {}).get('inputSettings', {})
        result.update(width=settings.get('width', 1920), height=settings.get('height', 1080))
        result['in_scene'] = bool(scene and await self._get_scene_item_id_by_scene_name(scene, name) is not None)
        result['configured'] = bool(result['in_scene'] and settings.get('url') == url
                                    and not settings.get('is_local_file'))
        if not self._is_overlay_url(settings.get('url', '')) or settings.get('is_local_file'):
            result['error'] = 'This browser source uses another page. Choose a different source name.'
        return result

    async def ensure_browser_source(self, scene: str, name: str, url: str) -> Dict[str, Any]:
        result = {'scene': scene, 'input_name': name, 'configured': False,
                  'created': False, 'added_to_scene': False, 'errors': []}
        async def request(kind, data=None):
            ok, response, _, error = await self._send_request_with_status(kind, data)
            if not ok:
                raise RuntimeError(f'{kind}: {error or "OBS request failed"}')
            return response or {}
        try:
            if not scene or scene not in (await self.list_scene_names() or []):
                raise ValueError('Select an existing OBS scene before adding the browser overlay.')
            kinds = (await request('GetInputKindList')).get('inputKinds', [])
            if 'browser_source' not in kinds:
                raise ValueError('This OBS installation does not provide Browser Source. Use an official OBS build.')
            video = await request('GetVideoSettings')
            width, height = int(video['baseWidth']), int(video['baseHeight'])
            settings = {'url': url, 'is_local_file': False, 'width': width, 'height': height,
                        'fps_custom': True, 'fps': 30, 'shutdown': False,
                        'restart_when_active': False, 'webpage_control_level': 0,
                        'css': 'html,body{background:transparent;margin:0;overflow:hidden;}'}
            source = next((item for item in await self._get_inputs() or [] if item.get('inputName') == name), None)
            new_item = False
            if source:
                if source.get('inputKind') != 'browser_source':
                    raise ValueError('Source name is already used by another input kind. Choose a different name.')
                previous = (await request('GetInputSettings', {'inputName': name})).get('inputSettings', {})
                if previous.get('is_local_file') or not self._is_overlay_url(previous.get('url', '')):
                    raise ValueError('Source name is already used by another webpage. Choose a different name.')
                await request('SetInputSettings', {'inputName': name, 'inputSettings': settings, 'overlay': True})
                item_id = await self._get_scene_item_id_by_scene_name(scene, name)
                if item_id is None:
                    data = await request('CreateSceneItem', {'sceneName': scene, 'sourceName': name, 'sceneItemEnabled': True})
                    item_id = data['sceneItemId']
                    result['added_to_scene'] = new_item = True
            else:
                data = await request('CreateInput', {'sceneName': scene, 'inputName': name,
                                                    'inputKind': 'browser_source', 'inputSettings': settings,
                                                    'sceneItemEnabled': True})
                item_id = data['sceneItemId']
                result['created'] = new_item = True
            if new_item:
                await request('SetSceneItemTransform', {'sceneName': scene, 'sceneItemId': item_id,
                              'sceneItemTransform': {'positionX': 0, 'positionY': 0, 'scaleX': 1, 'scaleY': 1,
                                                     'rotation': 0, 'alignment': 5, 'boundsType': 'OBS_BOUNDS_NONE'}})
            await request('SetSceneItemEnabled', {'sceneName': scene, 'sceneItemId': item_id, 'sceneItemEnabled': True})
            verified = await self.get_browser_source_status(scene, name, url)
            if not verified['configured'] or verified['width'] != width or verified['height'] != height:
                raise RuntimeError('OBS did not confirm the browser source settings. Refresh status and try again.')
            result.update(configured=True, width=width, height=height, url=url)
        except Exception as exc:
            result['errors'].append(str(exc))
        return result

    async def hide_legacy_text_sources(self, scene: str) -> list[str]:
        errors = []
        inputs = {item.get('inputName'): item.get('inputKind', '') for item in await self._get_inputs() or []}
        for name in REQUIRED_TEXT_SOURCES:
            if not inputs.get(name, '').startswith(('text_gdiplus', 'text_ft2')):
                continue
            item_id = await self._get_scene_item_id_by_scene_name(scene, name)
            if item_id is not None:
                ok, _, _, error = await self._send_request_with_status('SetSceneItemEnabled', {
                    'sceneName': scene, 'sceneItemId': item_id, 'sceneItemEnabled': False})
                if not ok:
                    errors.append(f'{name}: {error or "Could not hide old text source"}')
        return errors

    async def refresh_browser_source(self, name: str, url: str) -> bool:
        ok, data, _, _ = await self._send_request_with_status('GetInputSettings', {'inputName': name})
        if not ok or (data or {}).get('inputKind') != 'browser_source':
            return False
        settings = data.get('inputSettings', {})
        if settings.get('is_local_file') or not self._is_overlay_url(settings.get('url', '')):
            return False
        if settings.get('url') != url:
            ok, _, _, _ = await self._send_request_with_status('SetInputSettings', {
                'inputName': name, 'inputSettings': {'url': url}, 'overlay': True})
            if not ok:
                return False
        ok, _, _, _ = await self._send_request_with_status('PressInputPropertiesButton', {
            'inputName': name, 'propertyName': 'refreshnocache'})
        return ok

    async def _get_audio_capture_status(self, scene_key: str = 'main', exe_name: str = '', scene_name: Optional[str] = None,
                                        preferred_input_name: str = '') -> Optional[Dict[str, Any]]:
        scene = self._resolve_scene_name(scene_name=scene_name, scene_key=scene_key)
        inputs = await self._get_inputs()
        if inputs is None or not exe_name:
            return None
        owned_names = {preferred_input_name, f'{preferred_input_name} 2', f'{preferred_input_name} 3', f'{preferred_input_name} Capture'} if preferred_input_name else set()
        candidates = []
        for item in inputs:
            name = item.get('inputName')
            if not isinstance(name, str) or item.get('inputKind') != 'wasapi_process_output_capture':
                continue
            ok, response, _, _ = await self._send_request_with_status('GetInputSettings', {'inputName': name})
            if not ok:
                return None  # A failed inspection is not evidence that a capture is missing.
            settings = (response or {}).get('inputSettings') or {}
            selected_exe = _audio_window_executable(settings.get('window'))
            matches = selected_exe == exe_name.casefold()
            # Reuse our empty placeholders and repair the old Python-sidecar
            # target. A named source capturing another app may be intentional.
            repairable = name in owned_names and (not selected_exe or (
                preferred_input_name == 'TipTune Audio' and selected_exe == 'tiptune.exe'))
            if not matches and not repairable:
                continue
            item_id = None
            if scene:
                ok, item_response, code, _ = await self._send_request_with_status('GetSceneItemId', {
                    'sceneName': scene, 'sourceName': name}, max_retries=0)
                if not ok and code != 600:  # ResourceNotFound is the only confirmed missing item.
                    return None
                if ok:
                    item_id = (item_response or {}).get('sceneItemId')
                    if not isinstance(item_id, int):
                        return None
            candidates.append((int(matches) * 2 + int(item_id is not None), name, settings, item_id, matches))
        candidates.sort(key=lambda item: item[0], reverse=True)
        result = {'target_exe': exe_name, 'input_name': None, 'input_kind': None,
                  'input_exists': False, 'in_main_scene': False, 'target_matches': False,
                  'configured': False, 'present': False, 'state': 'needs_setup'}
        if not candidates:
            return result
        _, name, settings, item_id, matches = candidates[0]
        try:
            matches = matches and int(settings.get('priority', 0)) == 2
        except (TypeError, ValueError):
            matches = False
        result.update(input_name=name, input_kind='wasapi_process_output_capture', input_exists=True,
                      in_main_scene=item_id is not None, target_matches=matches,
                      selected_exe=_audio_window_executable(settings.get('window')))
        if not matches or item_id is None:
            return result
        enabled_ok, enabled, _, _ = await self._send_request_with_status('GetSceneItemEnabled', {'sceneName': scene, 'sceneItemId': item_id})
        mute_ok, mute, _, _ = await self._send_request_with_status('GetInputMute', {'inputName': name})
        if not enabled_ok or not mute_ok or not isinstance((enabled or {}).get('sceneItemEnabled'), bool) or not isinstance((mute or {}).get('inputMuted'), bool):
            result['state'] = 'unavailable'
            return result
        result.update(scene_item_enabled=enabled['sceneItemEnabled'], muted=mute['inputMuted'])
        result['state'] = 'disabled' if not enabled['sceneItemEnabled'] else 'muted' if mute['inputMuted'] else 'configured'
        result['configured'] = result['present'] = result['state'] == 'configured'
        return result

    async def get_spotify_audio_capture_status(self, scene_key: str = 'main', exe_name: str = 'Spotify.exe', scene_name: Optional[str] = None) -> Optional[Dict[str, Any]]:
        return await self._get_audio_capture_status(scene_key, exe_name, scene_name, 'Spotify Audio')

    async def get_app_audio_capture_status(self, scene_key: str = 'main', exe_name: str = '', scene_name: Optional[str] = None) -> Optional[Dict[str, Any]]:
        return await self._get_audio_capture_status(scene_key, exe_name, scene_name, 'TipTune Audio')

    async def get_cable_audio_capture_status(self, scene_name: str) -> Optional[Dict[str, Any]]:
        inputs = await self._get_inputs()
        if inputs is None:
            return None
        result = {'input_name': None, 'input_exists': False, 'in_main_scene': False,
                  'target_matches': False, 'configured': False, 'present': False, 'state': 'needs_setup'}
        candidates = []
        devices = None
        for item in inputs:
            name = item.get('inputName')
            if not name or item.get('inputKind') != 'wasapi_input_capture':
                continue
            if devices is None:
                ok, properties, _, _ = await self._send_request_with_status('GetInputPropertiesListPropertyItems', {
                    'inputName': name, 'propertyName': 'device_id'}, max_retries=0)
                if not ok:
                    return None
                devices = [device for device in (properties or {}).get('propertyItems', [])
                           if device.get('itemName') == CABLE_DEVICE and device.get('itemValue') not in ('', 'default', None)]
            ok, response, _, _ = await self._send_request_with_status('GetInputSettings', {'inputName': name}, max_retries=0)
            if not ok:
                return None
            selected = ((response or {}).get('inputSettings') or {}).get('device_id')
            device = next((device for device in devices if device.get('itemValue') == selected), None)
            placeholder = name in CABLE_SOURCE_NAMES and selected == CABLE_PLACEHOLDER
            if not device and not placeholder:
                continue  # Never repurpose another microphone or virtual bus.
            item_id = None
            if scene_name:
                ok, response, code, _ = await self._send_request_with_status('GetSceneItemId', {
                    'sceneName': scene_name, 'sourceName': name}, max_retries=0)
                if not ok and code != 600:
                    return None
                if ok:
                    item_id = (response or {}).get('sceneItemId')
                    if not isinstance(item_id, int):
                        return None
            matches = bool(device and device.get('itemEnabled', True))
            candidates.append((int(matches) * 2 + int(item_id is not None), name, item_id, matches, placeholder))
        if not candidates:
            return result
        _, name, item_id, matches, placeholder = max(candidates, key=lambda item: item[0])
        result.update(input_name=name, input_exists=True, input_kind='wasapi_input_capture',
                      in_main_scene=item_id is not None, target_matches=matches, placeholder=placeholder)
        if not matches or item_id is None:
            if not any(device.get('itemEnabled', True) for device in devices or []):
                result['state'] = 'device_missing'
            return result
        enabled_ok, enabled, _, _ = await self._send_request_with_status('GetSceneItemEnabled', {
            'sceneName': scene_name, 'sceneItemId': item_id}, max_retries=0)
        mute_ok, mute, _, _ = await self._send_request_with_status('GetInputMute', {'inputName': name}, max_retries=0)
        monitor_ok, monitor, _, _ = await self._send_request_with_status('GetInputAudioMonitorType', {'inputName': name}, max_retries=0)
        if not enabled_ok or not mute_ok or not monitor_ok or not isinstance((enabled or {}).get('sceneItemEnabled'), bool) or not isinstance((mute or {}).get('inputMuted'), bool):
            result['state'] = 'unavailable'
            return result
        monitoring = (monitor or {}).get('monitorType') == 'OBS_MONITORING_TYPE_MONITOR_AND_OUTPUT'
        result.update(scene_item_enabled=enabled['sceneItemEnabled'], muted=mute['inputMuted'], monitoring=monitoring)
        result['state'] = 'disabled' if not enabled['sceneItemEnabled'] else 'muted' if mute['inputMuted'] else 'needs_monitoring' if not monitoring else 'configured'
        result['configured'] = result['present'] = result['state'] == 'configured'
        return result

    async def ensure_cable_audio_capture(self, scene_name: str) -> Optional[Dict[str, Any]]:
        if not scene_name:
            return {'configured': False, 'errors': ['Choose an OBS scene and save it before setting up audio.']}
        status = await self.get_cable_audio_capture_status(scene_name)
        if status is None:
            return None
        name = status['input_name']
        result = {'scene': scene_name, 'input_name': name, 'created': False, 'added_to_scene': False,
                  'configured': False, 'errors': []}

        async def request(kind, data):
            ok, response, code, error = await self._send_request_with_status(kind, data, max_retries=0)
            if not ok:
                raise RuntimeError(error or f'{kind} failed ({code})')
            return response or {}

        try:
            if not name:
                inputs = await self._get_inputs()
                if inputs is None:
                    return None
                used = {item.get('inputName') for item in inputs}
                name = next((candidate for candidate in CABLE_SOURCE_NAMES if candidate not in used), None)
                if not name:
                    raise RuntimeError('Choose a free TipTune Audio source name in OBS.')
                # An explicit invalid device and hidden scene item avoid capturing
                # the default microphone while discovering the installed cable ID.
                await request('CreateInput', {'sceneName': scene_name, 'inputName': name,
                    'inputKind': 'wasapi_input_capture', 'inputSettings': {'device_id': CABLE_PLACEHOLDER},
                    'sceneItemEnabled': False})
                result.update(input_name=name, created=True)
            if not status['target_matches']:
                properties = await request('GetInputPropertiesListPropertyItems', {'inputName': name, 'propertyName': 'device_id'})
                device_id = next((item.get('itemValue') for item in properties.get('propertyItems', [])
                    if item.get('itemName') == CABLE_DEVICE and item.get('itemEnabled', True)
                    and item.get('itemValue') not in ('', 'default', None)), None)
                if not device_id:
                    raise RuntimeError('Enable CABLE Output (VB-Audio Virtual Cable) in Windows sound settings, then try again.')
                await request('SetInputSettings', {'inputName': name, 'inputSettings': {'device_id': device_id}, 'overlay': True})
            await request('SetInputAudioMonitorType', {'inputName': name, 'monitorType': 'OBS_MONITORING_TYPE_MONITOR_AND_OUTPUT'})
            if not result['created'] and not status['in_main_scene']:
                await request('CreateSceneItem', {'sceneName': scene_name, 'sourceName': name, 'sceneItemEnabled': True})
                result['added_to_scene'] = True
            if result['created'] or status.get('placeholder'):
                item = await request('GetSceneItemId', {'sceneName': scene_name, 'sourceName': name})
                await request('SetSceneItemEnabled', {'sceneName': scene_name, 'sceneItemId': item['sceneItemId'], 'sceneItemEnabled': True})
            verified = await self.get_cable_audio_capture_status(scene_name)
            if verified is None or verified['input_name'] != name:
                raise RuntimeError('Could not verify audio capture. Refresh status and try again.')
            result.update(verified)
            if verified['state'] not in ('configured', 'muted', 'disabled'):
                raise RuntimeError('OBS did not retain the requested audio settings. Refresh status and try again.')
        except Exception as exc:
            result['errors'].append(str(exc))
        return result

    async def ensure_spotify_audio_capture(self, scene_key: str = 'main', exe_name: str = 'Spotify.exe',
                                          preferred_input_name: str = 'Spotify Audio', scene_name: Optional[str] = None) -> Optional[Dict[str, Any]]:
        scene = self._resolve_scene_name(scene_name=scene_name, scene_key=scene_key)
        if not scene or not exe_name:
            return None
        status = await self._get_audio_capture_status(scene_key, exe_name, scene, preferred_input_name)
        if status is None:
            return None
        name = status.get('input_name')
        result = {'scene': scene, 'target_exe': exe_name, 'input_name': name, 'created': False,
                  'configured': False, 'added_to_scene': False, 'targets_exe': False, 'errors': []}

        async def request(kind, data):
            ok, response, code, error = await self._send_request_with_status(kind, data, max_retries=0)
            if not ok:
                result['errors'].append(error or f'{kind} failed ({code})')
            return ok, response or {}

        if not name:
            inputs = await self._get_inputs()
            if inputs is None:
                return None
            used = {item.get('inputName') for item in inputs}
            name = next((candidate for candidate in [preferred_input_name, f'{preferred_input_name} 2', f'{preferred_input_name} 3', f'{preferred_input_name} Capture'] if candidate not in used), None)
            if not name:
                result['errors'].append('Choose a free capture source name in OBS.')
                return result
            ok, _ = await request('CreateInput', {'sceneName': scene, 'inputName': name,
                'inputKind': 'wasapi_process_output_capture', 'inputSettings': {'priority': 2, 'window': ''}, 'sceneItemEnabled': True})
            if not ok:
                return result
            result.update(input_name=name, created=True)
        if not status.get('target_matches'):
            ok, properties = await request('GetInputPropertiesListPropertyItems', {'inputName': name, 'propertyName': 'window'})
            if not ok:
                return result
            window = next((item.get('itemValue') for item in properties.get('propertyItems', [])
                if isinstance(item, dict) and item.get('itemEnabled', True)
                and _audio_window_executable(item.get('itemValue')) == exe_name.casefold()), None)
            if not window:
                result['errors'].append('Open the playback app window, then try setting up capture again.')
                return result
            ok, _ = await request('SetInputSettings', {'inputName': name, 'inputSettings': {'priority': 2, 'window': window}, 'overlay': True})
            if not ok:
                return result
        if not result['created'] and not status.get('in_main_scene'):
            ok, _ = await request('CreateSceneItem', {'sceneName': scene, 'sourceName': name})
            if not ok:
                return result
            result['added_to_scene'] = True
        verified = await self._get_audio_capture_status(scene_key, exe_name, scene, preferred_input_name)
        if verified is None:
            result['errors'].append('Could not verify the capture settings. Refresh status and try again.')
            return result
        result.update(configured=verified['configured'], targets_exe=verified['target_matches'],
                      in_main_scene=verified['in_main_scene'], state=verified['state'])
        if verified['state'] == 'needs_setup':
            result['errors'].append('OBS did not retain the requested capture target. Check its input settings.')
        return result

    async def _create_text_input(self, scene_name: str, input_name: str) -> tuple[bool, Optional[str]]:
        if not scene_name or not input_name:
            return (False, 'invalid_scene_or_input')

        kinds = ['text_gdiplus_v3', 'text_gdiplus_v2', 'text_ft2_source']
        failures: list[str] = []
        for kind in kinds:
            input_settings: Dict[str, Any] = {'text': ''}
            if kind.startswith('text_gdiplus'):
                input_settings = dict(DEFAULT_TEXT_SETTINGS_GDIPLUS)

            ok, _, code, comment = await self._send_request_with_status('CreateInput', {
                'sceneName': scene_name,
                'inputName': input_name,
                'inputKind': kind,
                'inputSettings': input_settings,
                'sceneItemEnabled': False,
            }, max_retries=0)
            if ok:
                return (True, None)
            if code is not None or comment:
                failures.append(f"{kind}: {code if code is not None else ''} {comment or ''}".strip())
            else:
                failures.append(f"{kind}: failed")

        return (False, "; ".join(failures) if failures else 'create_input_failed')

    async def ensure_text_sources(self, scene_key: str = 'main', source_names: Optional[list[str]] = None, scene_name: Optional[str] = None) -> Optional[Dict[str, Any]]:
        names = source_names or list(REQUIRED_TEXT_SOURCES)
        resolved_scene = self._resolve_scene_name(scene_name=scene_name, scene_key=scene_key)
        if not resolved_scene:
            resolved_scene = await self.get_current_scene()
        if not resolved_scene:
            return None

        input_names = await self._get_input_names()
        if input_names is None:
            return None

        created: list[str] = []
        added_to_scene: list[str] = []
        already_present: list[str] = []
        errors: Dict[str, str] = {}

        for n in names:
            try:
                exists = n in input_names
                if not exists:
                    ok, err = await self._create_text_input(resolved_scene, n)
                    if not ok:
                        errors[n] = err or 'create_input_failed'
                        continue
                    created.append(n)
                    input_names.add(n)

                scene_item_id = await self._get_scene_item_id_by_scene_name(resolved_scene, n)
                if scene_item_id is None:
                    resp = await self.send_request('CreateSceneItem', {
                        'sceneName': resolved_scene,
                        'sourceName': n,
                    })
                    if not resp or 'sceneItemId' not in resp:
                        errors[n] = 'add_to_scene_failed'
                        continue
                    try:
                        scene_item_id = int(resp['sceneItemId'])
                    except Exception:
                        scene_item_id = None

                    if scene_item_id is None:
                        errors[n] = 'add_to_scene_failed'
                        continue
                    added_to_scene.append(n)

                await self.send_request('SetSceneItemEnabled', {
                    'sceneName': resolved_scene,
                    'sceneItemId': scene_item_id,
                    'sceneItemEnabled': False,
                })
                await self.send_request('SetInputSettings', {
                    'inputName': n,
                    'inputSettings': {'text': ''}
                })

                already_present.append(n)
            except Exception as exc:
                errors[n] = str(exc)

        return {
            'scene': resolved_scene,
            'created': created,
            'added_to_scene': added_to_scene,
            'already_present': already_present,
            'errors': errors,
        }

    async def set_source_visibility(self, scene_key: str, source_name: str, visible: bool) -> bool:
        """Set the visibility of a source in a scene.

        Args:
            scene_key: Key of the scene in the YAML config
            source_name: Name of source to modify
            visible: Whether source should be visible

        Returns:
            True if successful, False otherwise
        """
        scene_name = self._get_scene_name(scene_key)
        if not scene_name:
            return False

        logger.debug("obs.source.visibility",
                    message="Setting source visibility",
                    data={
                        "scene": scene_name,
                        "source": source_name,
                        "visible": visible
                    })

        # First get the scene item ID
        id_response = await self.send_request('GetSceneItemId', {
            'sceneName': scene_name,
            'sourceName': source_name
        })

        # GetSceneItemId must return a valid ID
        if not id_response or 'sceneItemId' not in id_response:
            logger.error("obs.source.error",
                        message="Failed to get scene item ID",
                        data={"source": source_name})
            return False

        scene_item_id = id_response['sceneItemId']

        # Then set the visibility using the ID
        try:
            # For SetSceneItemEnabled, any non-error response (including None) means success
            await self.send_request('SetSceneItemEnabled', {
                'sceneName': scene_name,
                'sceneItemId': scene_item_id,
                'sceneItemEnabled': visible
            })

            # If we got here, the request succeeded (no exception was raised)
            logger.info("obs.source.visibility.success",
                       message="Set source visibility",
                       data={
                           "scene": scene_name,
                           "source": source_name,
                           "visible": visible
                       })
            return True

        except Exception as exc:
            logger.exception("obs.source.visibility.error",
                           message="Failed to set source visibility",
                           exc=exc,
                           data={
                               "scene": scene_name,
                               "source": source_name,
                               "visible": visible,
                               "error_type": type(exc).__name__
                           })
            return False

    async def get_source_visibility(self, scene_key: str, source_name: str) -> Optional[bool]:
        """Get the visibility state of a source in a scene.

        Args:
            scene_key: Key of the scene in the YAML config
            source_name: Name of source to check

        Returns:
            True if source is visible, False if hidden, None if request failed
        """
        scene_name = self._get_scene_name(scene_key)
        if not scene_name:
            return None

        logger.debug("obs.source.get",
                    message="Getting source visibility",
                    data={
                        "scene": scene_name,
                        "source": source_name
                    })

        # First get the scene item ID
        id_response = await self.send_request('GetSceneItemId', {
            'sceneName': scene_name,
            'sourceName': source_name
        })

        if not id_response:
            logger.error("obs.source.error",
                        message="Failed to get scene item ID",
                        data={"source": source_name})
            return None

        scene_item_id = id_response.get('sceneItemId')
        if scene_item_id is None:
            logger.error("obs.source.notfound",
                        message="Scene item ID not found",
                        data={"source": source_name})
            return None

        # Then get the visibility using the ID
        response = await self.send_request('GetSceneItemEnabled', {
            'sceneName': scene_name,
            'sceneItemId': scene_item_id
        })

        if response is not None:
            visible = response.get('sceneItemEnabled', False)
            logger.debug("obs.source.visibility.get",
                        message="Retrieved source visibility",
                        data={
                            "scene": scene_name,
                            "source": source_name,
                            "visible": visible
                        })
            return visible

        return None

    async def show_song_requester(self, requester_name: str, song_details: str) -> bool:
        """Show the song requester overlay with specified details.

        Args:
            requester_name: Name of the person requesting the song
            song_details: Details of the requested song

        Returns:
            True if successful, False otherwise
        """
        try:
            logger.debug("obs.overlay.show",
                        message="Showing song requester overlay",
                        data={
                            "requester": requester_name,
                            "song": song_details
                        })

            request_content = {
                'inputName': 'SongRequester',
                'inputSettings': {
                    'text': f'Song requested by {requester_name}\n{song_details}'
                }
            }
            logger.debug("obs.overlay.show.request",
                        message="Sending request to set input settings",
                        data={"request": request_content})

            try:
                # SetInputSettings returns None on success
                await self.send_request('SetInputSettings', request_content)
                text_success = True
            except Exception:
                text_success = False

            await asyncio.sleep(1)

            visibility_success = await self.set_source_visibility('main', 'SongRequester', True)

            if text_success and visibility_success:
                logger.info("obs.overlay.show.success",
                           message="Song requester overlay shown")
                return True

            logger.error("obs.overlay.show.error",
                        message="Failed to show song requester overlay",
                        data={
                            "text_success": text_success,
                            "visibility_success": visibility_success
                        })
            return False

        except Exception as exc:
            logger.exception("obs.overlay.show.error",
                           message="Failed to show song requester overlay",
                           exc=exc,
                           data={
                               "error_type": type(exc).__name__
                           })
            return False

    async def hide_song_requester(self) -> bool:
        """Hide the song requester overlay.

        Returns:
            True if successful, False otherwise
        """
        try:
            logger.debug("obs.overlay.hide",
                        message="Hiding song requester overlay")

            visibility_success = await self.set_source_visibility('main', 'SongRequester', False)

            if visibility_success:
                logger.info("obs.overlay.hide.success",
                           message="Song requester overlay hidden")
            else:
                logger.error("obs.overlay.hide.error",
                           message="Failed to hide song requester overlay",
                           data={"visibility_success": visibility_success})

            await asyncio.sleep(1)

            request_content = {
                'inputName': 'SongRequester',
                'inputSettings': {
                    'text': ''
                }
            }
            logger.debug("obs.overlay.hide.request",
                        message="Sending request to set input settings",
                        data={"request": request_content})

            try:
                # SetInputSettings returns None on success
                await self.send_request('SetInputSettings', request_content)
                text_success = True
            except Exception:
                text_success = False

            return visibility_success and text_success

        except Exception as exc:
            logger.exception("obs.overlay.hide.error",
                           message="Failed to hide song requester overlay",
                           exc=exc,
                           data={
                               "error_type": type(exc).__name__
                           })
            return False

    async def trigger_song_requester_overlay(self, requester_name: str, song_details: str, display_duration: int = 10) -> None:
        """Show the song requester overlay for a specified duration.

        Args:
            requester_name: Name of the person requesting the song
            song_details: Details of the requested song
            display_duration: How long to display the overlay in seconds
        """
        logger.debug("obs.overlay.trigger",
                    message="Triggering song requester overlay",
                    data={
                        "requester": requester_name,
                        "song": song_details,
                        "duration": display_duration
                    })

        await self.show_song_requester(requester_name, song_details)
        await asyncio.sleep(display_duration)
        await self.hide_song_requester()

    async def show_warning_overlay(self, username: str, warning_message: str) -> bool:
        """Show the warning overlay with specified details.

        Args:
            username: Name of the user the warning is for
            warning_message: Warning message to display

        Returns:
            True if successful, False otherwise
        """
        try:
            logger.debug("obs.overlay.warning.show",
                        message="Showing warning overlay",
                        data={
                            "username": username,
                            "message": warning_message
                        })

            request_content = {
                'inputName': 'WarningOverlay',  # Make sure this matches your OBS text source name
                'inputSettings': {
                    'text': f'Notice for {username}:\n{warning_message}'
                }
            }
            logger.debug("obs.overlay.warning.request",
                        message="Sending request to set input settings",
                        data={"request": request_content})

            try:
                # SetInputSettings returns None on success
                await self.send_request('SetInputSettings', request_content)
                text_success = True
            except Exception:
                text_success = False

            await asyncio.sleep(1)

            visibility_success = await self.set_source_visibility('main', 'WarningOverlay', True)

            if text_success and visibility_success:
                logger.info("obs.overlay.warning.success",
                           message="Warning overlay shown")
                return True

            logger.error("obs.overlay.warning.error",
                        message="Failed to show warning overlay",
                        data={
                            "text_success": text_success,
                            "visibility_success": visibility_success
                        })
            return False

        except Exception as exc:
            logger.exception("obs.overlay.warning.error",
                           message="Failed to show warning overlay",
                           exc=exc,
                           data={
                               "error_type": type(exc).__name__
                           })
            return False

    async def hide_warning_overlay(self) -> bool:
        """Hide the warning overlay.

        Returns:
            True if successful, False otherwise
        """
        try:
            logger.debug("obs.overlay.warning.hide",
                        message="Hiding warning overlay")

            visibility_success = await self.set_source_visibility('main', 'WarningOverlay', False)

            if visibility_success:
                logger.info("obs.overlay.warning.hide.success",
                           message="Warning overlay hidden")
            else:
                logger.error("obs.overlay.warning.hide.error",
                           message="Failed to hide warning overlay",
                           data={"visibility_success": visibility_success})

            await asyncio.sleep(1)

            request_content = {
                'inputName': 'WarningOverlay',
                'inputSettings': {
                    'text': ''
                }
            }
            logger.debug("obs.overlay.warning.hide.request",
                        message="Sending request to clear input settings",
                        data={"request": request_content})

            try:
                # SetInputSettings returns None on success
                await self.send_request('SetInputSettings', request_content)
                text_success = True
            except Exception:
                text_success = False

            return visibility_success and text_success

        except Exception as exc:
            logger.exception("obs.overlay.warning.hide.error",
                           message="Failed to hide warning overlay",
                           exc=exc,
                           data={
                               "error_type": type(exc).__name__
                           })
            return False

    async def trigger_warning_overlay(self, username: str, warning_message: str, display_duration: int = 10) -> None:
        """Show the warning overlay for a specified duration.

        Args:
            username: Name of the user the warning is for
            warning_message: Warning message to display
            display_duration: How long to display the overlay in seconds
        """
        logger.debug("obs.overlay.warning.trigger",
                    message="Triggering warning overlay",
                    data={
                        "username": username,
                        "message": warning_message,
                        "duration": display_duration
                    })

        await self.show_warning_overlay(username, warning_message)
        await asyncio.sleep(display_duration)
        await self.hide_warning_overlay()

    async def show_motor_overlay(self, message: str, overlay_type: str) -> bool:
        """Show the motor action overlay with specified details.

        Args:
            message: The message to display on the overlay
            overlay_type: Type of overlay ('queue_addition' or 'processing')

        Returns:
            True if successful, False otherwise
        """
        try:
            logger.debug("obs.overlay.motor.show",
                        message="Showing motor action overlay",
                        data={
                            "message": message,
                            "type": overlay_type
                        })

            request_content = {
                'inputName': 'GeneralOverlay',  # Make sure this matches your OBS text source name
                'inputSettings': {
                    'text': message
                }
            }
            logger.debug("obs.overlay.motor.request",
                        message="Sending request to set input settings",
                        data={"request": request_content})

            try:
                # SetInputSettings returns None on success
                await self.send_request('SetInputSettings', request_content)
                text_success = True
            except Exception:
                text_success = False

            await asyncio.sleep(1)

            visibility_success = await self.set_source_visibility('main', 'GeneralOverlay', True)

            if text_success and visibility_success:
                logger.info("obs.overlay.motor.success",
                           message="Motor action overlay shown",
                           data={"type": overlay_type})
                return True

            logger.error("obs.overlay.motor.error",
                        message="Failed to show motor action overlay",
                        data={
                            "text_success": text_success,
                            "visibility_success": visibility_success,
                            "type": overlay_type
                        })
            return False

        except Exception as exc:
            logger.exception("obs.overlay.motor.error",
                           message="Failed to show motor action overlay",
                           exc=exc,
                           data={
                               "error_type": type(exc).__name__,
                               "type": overlay_type
                           })
            return False

    async def hide_motor_overlay(self) -> bool:
        """Hide the motor action overlay.

        Returns:
            True if successful, False otherwise
        """
        try:
            logger.debug("obs.overlay.motor.hide",
                        message="Hiding motor action overlay")

            visibility_success = await self.set_source_visibility('main', 'GeneralOverlay', False)

            if visibility_success:
                logger.info("obs.overlay.motor.hide.success",
                           message="Motor action overlay hidden")
            else:
                logger.error("obs.overlay.motor.hide.error",
                           message="Failed to hide motor action overlay",
                           data={"visibility_success": visibility_success})

            await asyncio.sleep(1)

            request_content = {
                'inputName': 'GeneralOverlay',
                'inputSettings': {
                    'text': ''
                }
            }
            logger.debug("obs.overlay.motor.hide.request",
                        message="Sending request to clear input settings",
                        data={"request": request_content})

            try:
                # SetInputSettings returns None on success
                await self.send_request('SetInputSettings', request_content)
                text_success = True
            except Exception:
                text_success = False

            return visibility_success and text_success

        except Exception as exc:
            logger.exception("obs.overlay.motor.hide.error",
                           message="Failed to hide motor action overlay",
                           exc=exc,
                           data={
                               "error_type": type(exc).__name__
                           })
            return False

    async def trigger_motor_overlay(self, message: str, overlay_type: str, display_duration: int = 3) -> None:
        """Show the motor action overlay for a specified duration.

        Args:
            message: The message to display on the overlay
            overlay_type: Type of overlay ('queue_addition' or 'processing')
            display_duration: How long to display the overlay in seconds
        """
        logger.debug("obs.overlay.motor.trigger",
                    message="Triggering motor action overlay",
                    data={
                        "message": message,
                        "type": overlay_type,
                        "duration": display_duration
                    })

        await self.show_motor_overlay(message, overlay_type)
        await asyncio.sleep(display_duration)
        await self.hide_motor_overlay()

    async def show_now_playing_overlay(self, message: str) -> bool:
        try:
            request_content = {
                'inputName': 'NowPlayingOverlay',
                'inputSettings': {
                    'text': message
                }
            }

            try:
                await self.send_request('SetInputSettings', request_content)
                text_success = True
            except Exception:
                text_success = False

            await asyncio.sleep(1)
            visibility_success = await self.set_source_visibility('main', 'NowPlayingOverlay', True)
            return bool(text_success and visibility_success)
        except Exception as exc:
            logger.exception(
                "obs.overlay.now_playing.show.error",
                message="Failed to show now playing overlay",
                exc=exc,
            )
            return False

    async def hide_now_playing_overlay(self) -> bool:
        try:
            visibility_success = await self.set_source_visibility('main', 'NowPlayingOverlay', False)
            await asyncio.sleep(1)

            request_content = {
                'inputName': 'NowPlayingOverlay',
                'inputSettings': {
                    'text': ''
                }
            }
            try:
                await self.send_request('SetInputSettings', request_content)
                text_success = True
            except Exception:
                text_success = False

            return bool(visibility_success and text_success)
        except Exception as exc:
            logger.exception(
                "obs.overlay.now_playing.hide.error",
                message="Failed to hide now playing overlay",
                exc=exc,
            )
            return False

    async def trigger_now_playing_overlay(self, message: str, display_duration: int = 10) -> None:
        logger.debug(
            "obs.overlay.now_playing.trigger",
            message="Triggering now playing overlay",
            data={
                "duration": display_duration,
            },
        )

        await self.show_now_playing_overlay(message)
        await asyncio.sleep(display_duration)
        await self.hide_now_playing_overlay()

    def trigger_motor_overlay_sync(self, message: str, overlay_type: str, display_duration: int = 3) -> None:
        """Trigger motor action overlay synchronously.
        
        Args:
            message: The message to display on the overlay
            overlay_type: Type of overlay ('queue_addition' or 'processing')
            display_duration: How long to display the overlay in seconds
        """
        self.run_sync(self.trigger_motor_overlay(message, overlay_type, display_duration))

    def trigger_now_playing_overlay_sync(self, message: str, display_duration: int = 10) -> None:
        self.run_sync(self.trigger_now_playing_overlay(message, display_duration))

    async def initialize(self) -> bool:
        """Initialize the OBS handler and connect to WebSocket.
        This should be called after instantiation.
        """
        try:
            return await self.connect()
        except Exception as exc:
            logger.exception("obs.init.error",
                           exc=exc,
                           message="Failed to initialize OBS handler")
            return False

    def run_sync(self, coro):
        """Run a coroutine synchronously using the dedicated event loop.

        Args:
            coro: Coroutine to run

        Returns:
            Result of the coroutine or None if it failed
        """
        try:
            # If we're in a running event loop, use run_coroutine_threadsafe
            if self.loop.is_running():
                logger.debug("obs.sync.run",
                           message="Using run_coroutine_threadsafe")

                # For trigger_song_requester_overlay, use a longer timeout
                # that accounts for display duration
                timeout = 30  # Default timeout
                if isinstance(coro, asyncio.Task):
                    coro_name = coro.get_name()
                else:
                    coro_name = coro.__name__

                if 'trigger_song_requester_overlay' in coro_name:
                    # Add 5 seconds to display_duration for setup/cleanup
                    display_duration = coro.cr_frame.f_locals.get('display_duration', 10)
                    timeout = display_duration + 5

                logger.debug("obs.sync.timeout",
                           message="Setting coroutine timeout",
                           data={
                               "coroutine": coro_name,
                               "timeout": timeout
                           })

                future = asyncio.run_coroutine_threadsafe(coro, self.loop)
                return future.result(timeout=timeout)
            else:
                # If loop isn't running, use run_until_complete
                logger.debug("obs.sync.run",
                           message="Using run_until_complete")
                return self.loop.run_until_complete(coro)

        except asyncio.TimeoutError as exc:
            logger.error("obs.sync.timeout",
                        message="Coroutine timed out",
                        data={
                            "coroutine": coro_name if 'coro_name' in locals() else str(coro),
                            "timeout": timeout if 'timeout' in locals() else 'unknown'
                        })
            return None
        except Exception as exc:
            logger.exception("obs.sync.error",
                           exc=exc,
                           message="Error running coroutine",
                           data={
                               "coroutine": coro_name if 'coro_name' in locals() else str(coro)
                           })
            return None

    def connect_sync(self) -> bool:
        """Synchronous version of connect()."""
        return self.run_sync(self.connect())

    def disconnect_sync(self) -> None:
        """Synchronous version of disconnect()."""
        self.run_sync(self.disconnect())

    def set_scene_sync(self, scene_key: str) -> bool:
        """Set scene synchronously."""
        result = self.run_sync(self.set_scene(scene_key))
        return bool(result)

    def get_current_scene_sync(self) -> Optional[str]:
        """Get current scene synchronously."""
        return self.run_sync(self.get_current_scene())

    def set_source_visibility_sync(self, scene_key: str, source_name: str, visible: bool) -> bool:
        """Set source visibility synchronously."""
        result = self.run_sync(self.set_source_visibility(scene_key, source_name, visible))
        return bool(result)

    def get_source_visibility_sync(self, scene_key: str, source_name: str) -> Optional[bool]:
        """Get source visibility synchronously."""
        return self.run_sync(self.get_source_visibility(scene_key, source_name))

    def trigger_song_requester_overlay_sync(self, requester_name: str, song_details: str, display_duration: int = 10) -> None:
        """Trigger song requester overlay synchronously."""
        # Use display_duration + 1 second as timeout to ensure full display
        self.run_sync(self.trigger_song_requester_overlay(requester_name, song_details, display_duration))

    def trigger_warning_overlay_sync(self, username: str, warning_message: str, display_duration: int = 10) -> None:
        """Trigger warning overlay synchronously."""
        self.run_sync(self.trigger_warning_overlay(username, warning_message, display_duration))
