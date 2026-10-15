import os
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch

from handlers.obshandler import CABLE_DEVICE, CABLE_PLACEHOLDER, OBSHandler
from utils.audio_capture import desktop_audio_executable, process_executable


HOST = 'tiptune-tauri-bin.exe'
WINDOW = f'TipTune:Chrome_WidgetWin_1:{HOST}'


class DesktopAudioHostTests(unittest.TestCase):
    def test_uses_tauri_parent_executable_instead_of_python_sidecar(self):
        with patch.dict(os.environ, {'TIPTUNE_PARENT_PID': '1234'}), \
                patch('utils.audio_capture.process_executable', return_value=HOST) as executable:
            self.assertEqual(desktop_audio_executable(), HOST)
            executable.assert_called_once_with(1234)

    def test_missing_invalid_or_self_parent_does_not_guess_a_target(self):
        for value in ['', 'invalid', '-1', '0', str(os.getpid())]:
            with self.subTest(value=value), patch.dict(os.environ, {'TIPTUNE_PARENT_PID': value}), \
                    patch('utils.audio_capture.process_executable') as executable:
                self.assertIsNone(desktop_audio_executable())
                executable.assert_not_called()

    def test_exited_or_inaccessible_parent_does_not_guess_a_target(self):
        with patch.dict(os.environ, {'TIPTUNE_PARENT_PID': '1234'}), \
                patch('utils.audio_capture.process_executable', side_effect=OSError):
            self.assertIsNone(desktop_audio_executable())

    @unittest.skipUnless(sys.platform == 'win32', 'Windows process query')
    def test_windows_query_resolves_real_executable_and_missing_process(self):
        self.assertEqual(process_executable(os.getpid()).lower(), Path(sys.executable).name.lower())
        self.assertIsNone(process_executable(0xFFFFFFFE))


class AudioCaptureTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.obs = OBSHandler.__new__(OBSHandler)
        self.sources = {}
        self.items = {}
        self.windows = [{'itemValue': WINDOW, 'itemEnabled': True}]
        self.failures = {}
        self.ignore_writes = False
        self.obs._get_inputs = AsyncMock(side_effect=lambda: [
            {'inputName': name, 'inputKind': value['kind']} for name, value in self.sources.items()])
        self.obs._send_request_with_status = AsyncMock(side_effect=self.request)

    def add(self, name='TipTune Audio', window=WINDOW, priority=2, in_scene=True, enabled=True, muted=False,
            kind='wasapi_process_output_capture'):
        self.sources[name] = {'kind': kind, 'settings': {'window': window, 'priority': priority}, 'muted': muted}
        if in_scene:
            self.items[name] = enabled

    async def request(self, kind, data=None, **kwargs):
        if kind in self.failures:
            return False, None, 500, self.failures[kind]
        response = None
        if kind == 'GetInputSettings':
            response = {'inputSettings': dict(self.sources[data['inputName']]['settings'])}
        elif kind == 'GetSceneItemId':
            if data['sourceName'] not in self.items:
                return False, None, 600, 'Not found'
            response = {'sceneItemId': list(self.items).index(data['sourceName'])}
        elif kind == 'GetSceneItemEnabled':
            response = {'sceneItemEnabled': list(self.items.values())[data['sceneItemId']]}
        elif kind == 'GetInputMute':
            response = {'inputMuted': self.sources[data['inputName']]['muted']}
        elif kind == 'GetInputPropertiesListPropertyItems':
            response = {'propertyItems': list(self.windows)}
        elif kind == 'CreateInput':
            self.add(data['inputName'], data['inputSettings']['window'], data['inputSettings']['priority'])
            response = {'sceneItemId': len(self.items) - 1}
        elif kind == 'SetInputSettings' and not self.ignore_writes:
            self.sources[data['inputName']]['settings'].update(data['inputSettings'])
        elif kind == 'CreateSceneItem':
            self.items[data['sourceName']] = True
            response = {'sceneItemId': len(self.items) - 1}
        return True, response, None, None

    async def status(self, exe=HOST):
        return await self.obs.get_app_audio_capture_status(exe_name=exe, scene_name='LIVE')

    async def ensure(self, exe=HOST, name='TipTune Audio'):
        return await self.obs.ensure_spotify_audio_capture(exe_name=exe, preferred_input_name=name, scene_name='LIVE')

    def mutation_kinds(self):
        return [call.args[0] for call in self.obs._send_request_with_status.await_args_list
                if call.args[0] in {'SetInputSettings', 'CreateInput', 'CreateSceneItem', 'SetInputMute', 'SetSceneItemEnabled'}]

    async def test_exact_window_executable_is_required_not_title_or_unrelated_settings(self):
        self.add(name='Other capture', window=f'{HOST}:Chrome_WidgetWin_1:chrome.exe')
        self.sources['Other capture']['settings']['last_target'] = HOST
        self.assertFalse((await self.status())['input_exists'])
        self.add(name='My capture', window=f'Title:Class:{HOST.upper()}')
        self.assertTrue((await self.status())['configured'])

    async def test_repairs_legacy_sidecar_target_in_place_and_accepts_empty_success_response(self):
        self.add(window='TipTune:Class:TipTune.exe')
        result = await self.ensure()
        self.assertTrue(result['configured'])
        self.assertFalse(result['created'])
        self.assertEqual(self.sources['TipTune Audio']['settings']['window'], WINDOW)
        self.assertEqual(self.mutation_kinds(), ['SetInputSettings'])

    async def test_repair_sets_executable_priority_instead_of_title_priority(self):
        self.add(priority=1)
        self.assertFalse((await self.status())['configured'])
        self.assertTrue((await self.ensure())['configured'])
        self.assertEqual(self.sources['TipTune Audio']['settings']['priority'], 2)

    async def test_failed_write_is_reported_and_never_claims_configuration(self):
        self.add(window='TipTune:Class:TipTune.exe')
        self.failures['SetInputSettings'] = 'Capture update failed'
        result = await self.ensure()
        self.assertFalse(result['configured'])
        self.assertEqual(result['errors'], ['Capture update failed'])

    async def test_verify_actual_settings_after_successful_write(self):
        self.add(window='TipTune:Class:TipTune.exe')
        self.ignore_writes = True
        result = await self.ensure()
        self.assertFalse(result['configured'])
        self.assertIn('did not retain', result['errors'][0])

    async def test_missing_app_window_retry_reuses_placeholder_without_duplicates(self):
        self.windows = []
        first = await self.ensure()
        self.assertFalse(first['configured'])
        self.assertEqual(len(self.sources), 1)
        self.windows = [{'itemValue': WINDOW, 'itemEnabled': True}]
        second = await self.ensure()
        self.assertTrue(second['configured'])
        self.assertFalse(second['created'])
        self.assertEqual(self.mutation_kinds().count('CreateInput'), 1)

    async def test_window_display_label_and_disabled_property_are_not_capture_matches(self):
        self.windows = [{'itemName': HOST, 'itemValue': f'{HOST}:Class:chrome.exe', 'itemEnabled': True},
                        {'itemValue': WINDOW, 'itemEnabled': False}]
        result = await self.ensure()
        self.assertFalse(result['configured'])
        self.assertNotIn('SetInputSettings', self.mutation_kinds())

    async def test_preserves_user_mute_and_scene_visibility_when_repairing(self):
        for muted, enabled, state in [(True, True, 'muted'), (False, False, 'disabled')]:
            with self.subTest(state=state):
                self.add(window='TipTune:Class:TipTune.exe', muted=muted, enabled=enabled)
                result = await self.ensure()
                self.assertTrue(result['targets_exe'])
                self.assertFalse(result['configured'])
                self.assertEqual(result['state'], state)
                self.assertNotIn('SetInputMute', self.mutation_kinds())
                self.assertNotIn('SetSceneItemEnabled', self.mutation_kinds())

    async def test_attaches_matching_input_without_duplicate_capture(self):
        self.add(name='My app audio', in_scene=False)
        result = await self.ensure()
        self.assertTrue(result['configured'])
        self.assertTrue(result['added_to_scene'])
        self.assertEqual(len(self.sources), 1)

    async def test_spotify_capture_does_not_retarget_existing_tiptune_capture_named_spotify_audio(self):
        self.add(name='Spotify Audio')
        self.windows.append({'itemValue': 'Spotify:Class:Spotify.exe', 'itemEnabled': True})
        result = await self.ensure('Spotify.exe', 'Spotify Audio')
        self.assertTrue(result['configured'])
        self.assertEqual(result['input_name'], 'Spotify Audio 2')
        self.assertEqual(self.sources['Spotify Audio']['settings']['window'], WINDOW)
        self.assertEqual((await self.status())['input_name'], 'Spotify Audio')

    async def test_other_source_kind_or_other_application_is_never_repurposed(self):
        for kind in ['browser_source', 'wasapi_process_output_capture']:
            with self.subTest(kind=kind):
                self.sources.clear(); self.items.clear()
                self.add(window='Other:Class:chrome.exe', kind=kind)
                result = await self.ensure()
                self.assertTrue(result['configured'])
                self.assertEqual(result['input_name'], 'TipTune Audio 2')
                self.assertEqual(self.sources['TipTune Audio']['settings']['window'], 'Other:Class:chrome.exe')

    async def test_failed_input_or_scene_inspection_does_not_create_capture(self):
        self.add()
        for request in ['GetInputSettings', 'GetSceneItemId']:
            with self.subTest(request=request):
                self.failures = {request: 'Connection lost'}
                self.assertIsNone(await self.ensure())
                self.assertEqual(self.mutation_kinds(), [])

    async def test_mute_or_visibility_read_failure_does_not_claim_ready(self):
        self.add()
        for request in ['GetInputMute', 'GetSceneItemEnabled']:
            with self.subTest(request=request):
                self.failures = {request: 'Connection lost'}
                result = await self.status()
                self.assertFalse(result['configured'])
                self.assertEqual(result['state'], 'unavailable')


class CableCaptureTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.obs = OBSHandler.__new__(OBSHandler)
        self.sources = {}
        self.items = {}
        self.devices = [{'itemName': CABLE_DEVICE, 'itemValue': 'cable-id', 'itemEnabled': True},
                        {'itemName': 'CABLE Out 16ch (VB-Audio Virtual Cable)', 'itemValue': 'other-id', 'itemEnabled': True}]
        self.failures = {}
        self.ignore_writes = False
        self.obs._get_inputs = AsyncMock(side_effect=lambda: [
            {'inputName': name, 'inputKind': value['kind']} for name, value in self.sources.items()])
        self.obs._send_request_with_status = AsyncMock(side_effect=self.request)

    def add(self, name='TipTune Audio', device='cable-id', in_scene=True, enabled=True, muted=False,
            monitor='OBS_MONITORING_TYPE_MONITOR_AND_OUTPUT', kind='wasapi_input_capture'):
        self.sources[name] = {'kind': kind, 'settings': {'device_id': device}, 'muted': muted, 'monitor': monitor}
        if in_scene:
            self.items[name] = enabled

    async def request(self, kind, data=None, **kwargs):
        if kind in self.failures:
            return False, None, 500, self.failures[kind]
        response = None
        if kind == 'GetInputSettings':
            response = {'inputSettings': dict(self.sources[data['inputName']]['settings'])}
        elif kind == 'GetSceneItemId':
            if data['sourceName'] not in self.items:
                return False, None, 600, 'Not found'
            response = {'sceneItemId': list(self.items).index(data['sourceName'])}
        elif kind == 'GetSceneItemEnabled':
            response = {'sceneItemEnabled': list(self.items.values())[data['sceneItemId']]}
        elif kind == 'GetInputMute':
            response = {'inputMuted': self.sources[data['inputName']]['muted']}
        elif kind == 'GetInputAudioMonitorType':
            response = {'monitorType': self.sources[data['inputName']]['monitor']}
        elif kind == 'GetInputPropertiesListPropertyItems':
            response = {'propertyItems': list(self.devices)}
        elif kind == 'CreateInput':
            self.add(data['inputName'], data['inputSettings']['device_id'], enabled=data['sceneItemEnabled'],
                     monitor='OBS_MONITORING_TYPE_NONE')
        elif kind == 'SetInputSettings' and not self.ignore_writes:
            self.sources[data['inputName']]['settings'].update(data['inputSettings'])
        elif kind == 'SetInputAudioMonitorType' and not self.ignore_writes:
            self.sources[data['inputName']]['monitor'] = data['monitorType']
        elif kind == 'SetSceneItemEnabled' and not self.ignore_writes:
            self.items[list(self.items)[data['sceneItemId']]] = data['sceneItemEnabled']
        elif kind == 'CreateSceneItem':
            self.items[data['sourceName']] = data['sceneItemEnabled']
        return True, response, None, None

    async def ensure(self):
        return await self.obs.ensure_cable_audio_capture('LIVE')

    async def status(self):
        return await self.obs.get_cable_audio_capture_status('LIVE')

    async def test_creates_disabled_placeholder_then_targets_exact_cable_and_enables_monitoring(self):
        result = await self.ensure()
        self.assertTrue(result['configured'])
        self.assertTrue(result['created'])
        calls = self.obs._send_request_with_status.await_args_list
        create = next(call.args[1] for call in calls if call.args[0] == 'CreateInput')
        self.assertEqual(create['inputSettings'], {'device_id': CABLE_PLACEHOLDER})
        self.assertFalse(create['sceneItemEnabled'])
        self.assertEqual(self.sources['TipTune Audio']['settings']['device_id'], 'cable-id')
        self.assertTrue(self.items['TipTune Audio'])
        self.assertEqual(self.sources['TipTune Audio']['monitor'], 'OBS_MONITORING_TYPE_MONITOR_AND_OUTPUT')

    async def test_missing_device_retry_reuses_safe_placeholder_without_capturing_default_microphone(self):
        self.devices = []
        result = await self.ensure()
        self.assertFalse(result['configured'])
        self.assertIn('Enable CABLE Output', result['errors'][0])
        self.assertFalse(self.items['TipTune Audio'])
        self.assertEqual(self.sources['TipTune Audio']['settings']['device_id'], CABLE_PLACEHOLDER)
        self.devices = [{'itemName': CABLE_DEVICE, 'itemValue': 'cable-id', 'itemEnabled': True}]
        self.assertTrue((await self.ensure())['configured'])
        self.assertEqual(len(self.sources), 1)

    async def test_never_selects_default_disabled_or_different_cable_device(self):
        self.devices = [{'itemName': CABLE_DEVICE, 'itemValue': 'default', 'itemEnabled': True},
                        {'itemName': CABLE_DEVICE, 'itemValue': 'cable-id', 'itemEnabled': False},
                        {'itemName': 'CABLE Out 16ch (VB-Audio Virtual Cable)', 'itemValue': 'other-id', 'itemEnabled': True}]
        self.assertFalse((await self.ensure())['configured'])
        self.assertEqual(self.sources['TipTune Audio']['settings']['device_id'], CABLE_PLACEHOLDER)

    async def test_never_repurposes_another_device_or_an_application_capture(self):
        self.add(device='usb-microphone')
        self.add(name='TipTune Audio 2', kind='wasapi_process_output_capture')
        result = await self.ensure()
        self.assertTrue(result['configured'])
        self.assertEqual(result['input_name'], 'TipTune Audio 3')
        self.assertEqual(self.sources['TipTune Audio']['settings']['device_id'], 'usb-microphone')
        self.assertEqual(self.sources['TipTune Audio 2']['kind'], 'wasapi_process_output_capture')

    async def test_reuses_existing_cable_and_adds_it_to_selected_scene(self):
        self.add(name='My cable', in_scene=False)
        result = await self.ensure()
        self.assertTrue(result['configured'])
        self.assertTrue(result['added_to_scene'])
        self.assertEqual(len(self.sources), 1)

    async def test_preserves_existing_mute_and_visibility(self):
        for muted, enabled, state in [(True, True, 'muted'), (False, False, 'disabled')]:
            with self.subTest(state=state):
                self.add(muted=muted, enabled=enabled)
                result = await self.ensure()
                self.assertFalse(result['configured'])
                self.assertEqual(result['state'], state)
                self.assertEqual(self.sources['TipTune Audio']['muted'], muted)
                self.assertEqual(self.items['TipTune Audio'], enabled)
        mutations = [call.args[0] for call in self.obs._send_request_with_status.await_args_list]
        self.assertNotIn('SetInputMute', mutations)
        self.assertNotIn('SetSceneItemEnabled', mutations)

    async def test_monitoring_is_required_and_setup_repairs_it(self):
        self.add(monitor='OBS_MONITORING_TYPE_NONE')
        self.assertEqual((await self.status())['state'], 'needs_monitoring')
        self.assertTrue((await self.ensure())['configured'])

    async def test_write_success_is_verified_against_retained_settings(self):
        self.ignore_writes = True
        result = await self.ensure()
        self.assertFalse(result['configured'])
        self.assertIn('did not retain', result['errors'][0])

    async def test_failed_inspections_do_not_create_or_claim_ready(self):
        self.add()
        for request in ['GetInputSettings', 'GetSceneItemId', 'GetInputPropertiesListPropertyItems']:
            with self.subTest(request=request):
                self.failures = {request: 'Connection lost'}
                self.assertIsNone(await self.ensure())
        self.assertNotIn('CreateInput', [call.args[0] for call in self.obs._send_request_with_status.await_args_list])
        for request in ['GetInputMute', 'GetSceneItemEnabled', 'GetInputAudioMonitorType']:
            with self.subTest(request=request):
                self.failures = {request: 'Connection lost'}
                self.assertEqual((await self.status())['state'], 'unavailable')

    async def test_failed_write_is_reported(self):
        self.failures['SetInputSettings'] = 'Audio device update failed'
        result = await self.ensure()
        self.assertFalse(result['configured'])
        self.assertEqual(result['errors'], ['Audio device update failed'])


if __name__ == '__main__':
    unittest.main()
