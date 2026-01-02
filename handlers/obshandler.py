import asyncio
from typing import Any, Dict, Optional

import simpleobsws
import yaml

from utils.runtime_paths import get_resource_path
from utils.structured_logging import get_structured_logger

logger = get_structured_logger('tiptune.handlers.obshandler')

REQUIRED_TEXT_SOURCES = ['SongRequester', 'WarningOverlay', 'GeneralOverlay']

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

    async def get_text_source_status(self, scene_key: str = 'main', source_names: Optional[list[str]] = None) -> Optional[Dict[str, Any]]:
        names = source_names or list(REQUIRED_TEXT_SOURCES)

        current_scene = await self.get_current_scene()
        main_scene = self._get_scene_name(scene_key) if scene_key else None

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

    async def _create_text_input(self, scene_name: str, input_name: str) -> bool:
        if not scene_name or not input_name:
            return False

        kinds = ['text_gdiplus_v2', 'text_ft2_source']
        for kind in kinds:
            resp = await self.send_request('CreateInput', {
                'sceneName': scene_name,
                'inputName': input_name,
                'inputKind': kind,
                'inputSettings': {'text': ''},
                'sceneItemEnabled': False,
            })
            if resp is not None:
                return True
        return False

    async def ensure_text_sources(self, scene_key: str = 'main', source_names: Optional[list[str]] = None) -> Optional[Dict[str, Any]]:
        names = source_names or list(REQUIRED_TEXT_SOURCES)
        scene_name = self._get_scene_name(scene_key) if scene_key else None
        if not scene_name:
            scene_name = await self.get_current_scene()
        if not scene_name:
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
                    ok = await self._create_text_input(scene_name, n)
                    if not ok:
                        errors[n] = 'create_input_failed'
                        continue
                    created.append(n)
                    input_names.add(n)

                scene_item_id = await self._get_scene_item_id_by_scene_name(scene_name, n)
                if scene_item_id is None:
                    resp = await self.send_request('CreateSceneItem', {
                        'sceneName': scene_name,
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
                    'sceneName': scene_name,
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
            'scene': scene_name,
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

    def trigger_motor_overlay_sync(self, message: str, overlay_type: str, display_duration: int = 3) -> None:
        """Trigger motor action overlay synchronously.
        
        Args:
            message: The message to display on the overlay
            overlay_type: Type of overlay ('queue_addition' or 'processing')
            display_duration: How long to display the overlay in seconds
        """
        self.run_sync(self.trigger_motor_overlay(message, overlay_type, display_duration))

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
