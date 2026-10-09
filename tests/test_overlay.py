import asyncio
import configparser
import importlib
import json
import logging
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from aiohttp.test_utils import TestClient, TestServer
from utils.overlay import OverlayService, read_settings, validate_settings
from helpers.actions import Actions
from handlers.obshandler import OBSHandler


def config(mode='browser'):
    cfg = configparser.ConfigParser()
    cfg.read_dict({'Overlay': {'mode': mode}})
    return cfg


class OverlayStateTests(unittest.TestCase):
    def setUp(self):
        self.now = 1000.0
        self.display = OverlayService(config(), clock=lambda: self.now)

    def test_existing_configs_preserve_text_or_off(self):
        cfg = configparser.ConfigParser()
        self.assertEqual(read_settings(cfg)['mode'], 'browser')
        cfg.read_dict({'OBS': {'enabled': 'true'}})
        self.assertEqual(read_settings(cfg)['mode'], 'text')
        cfg['OBS']['enabled'] = 'false'
        self.assertEqual(read_settings(cfg)['mode'], 'off')

    def test_config_validation_is_bounded(self):
        for values in ({'scale': '49'}, {'queue_length': '6'}, {'opacity': '101'},
                       {'margin': '-1'}, {'accent_color': 'red'}, {'source_name': ''},
                       {'show_queue': 'sometimes'}, {'position': 'center'}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                validate_settings(values)

    def test_active_alerts_and_backlog_are_bounded_and_expire(self):
        for i in range(30):
            self.display.alert('request', str(i), duration=10)
        self.assertEqual(len(self.display.active), 3)
        self.assertEqual(len(self.display.pending), 20)
        self.assertEqual(self.display.pending[0]['message'], '10')
        self.now += 10
        self.display.tick()
        self.assertEqual([a['message'] for a in self.display.active], ['10', '11', '12'])
        self.now += 61
        self.display.tick()
        self.assertEqual(self.display.snapshot()['alerts'], [])
        self.assertFalse(self.display.pending)

    def test_independent_expiration_and_reconnect_snapshot(self):
        self.display.alert('request', 'first', duration=2)
        self.now += 1
        self.display.alert('warning', 'second', duration=10)
        self.now += 2
        self.display.tick()
        q = self.display.subscribe()
        self.assertEqual([a['message'] for a in q.get_nowait()['alerts']], ['second'])
        self.display.unsubscribe(q)

    def test_subscription_coalesces_and_cannot_miss_initial_updates(self):
        q = self.display.subscribe()
        self.display.alert('general', 'changed')
        self.assertEqual(q.qsize(), 1)
        self.assertEqual(q.get_nowait()['alerts'][0]['message'], 'changed')

    def test_public_payload_excludes_private_fields_and_keeps_duplicates(self):
        items = [{'queue_entry_id': str(i), 'source': 'spotify', 'name': 'Same song',
                  'requester': f'user{i}', 'uri': 'private', 'tip_message': 'secret',
                  'password': 'secret'} for i in range(5)]
        self.display.update_tracks(items[0], items, {'queue_paused': True})
        state = self.display.snapshot()
        self.assertEqual(state['total_queue_count'], 5)
        self.assertEqual(len(state['upcoming']), 3)
        self.assertEqual(state['now_playing']['requester'], 'user0')
        self.assertNotIn('private', json.dumps(state))
        self.assertNotIn('secret', json.dumps(state))

    def test_mode_change_clears_announcements(self):
        self.display.alert('request', 'hello')
        self.display.configure(config('off'))
        self.display.alert('request', 'hidden')
        self.assertEqual(self.display.snapshot()['alerts'], [])


class OverlayDispatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_browser_alert_does_not_wait_or_require_obs(self):
        actions = Actions.__new__(Actions)
        actions.obs_integration_enabled = False
        actions.request_overlay_duration = 10
        actions.overlay_service = OverlayService(config())
        await asyncio.wait_for(actions.trigger_song_requester_overlay('alice', 'Song', 100), .1)
        await asyncio.wait_for(actions.trigger_warning_overlay('alice', 'Notice', 100), .1)
        await asyncio.wait_for(actions.trigger_queue_state_overlay('Queue resumed'), .1)
        self.assertEqual(len(actions.overlay_service.active), 3)

    async def test_text_and_off_routing(self):
        actions = Actions.__new__(Actions)
        actions.obs_integration_enabled = True
        actions.obs = SimpleNamespace(trigger_song_requester_overlay=AsyncMock())
        actions.overlay_service = OverlayService(config('text'))
        await actions.trigger_song_requester_overlay('alice', 'Song', 10)
        actions.obs.trigger_song_requester_overlay.assert_awaited_once()
        actions.overlay_service.configure(config('off'))
        await actions.trigger_song_requester_overlay('alice', 'Song', 10)
        actions.obs.trigger_song_requester_overlay.assert_awaited_once()


class BrowserSourceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.obs = OBSHandler.__new__(OBSHandler)
        self.sources = {}
        self.items = {}
        self.transforms = []
        self.obs.list_scene_names = AsyncMock(return_value=['LIVE'])
        self.obs._get_inputs = AsyncMock(side_effect=lambda: [dict(inputName=n, inputKind=v['kind']) for n, v in self.sources.items()])
        self.obs._get_scene_item_id_by_scene_name = AsyncMock(side_effect=lambda s, n: self.items.get((s, n)))
        self.obs._send_request_with_status = AsyncMock(side_effect=self.request)

    async def request(self, kind, data=None, **kwargs):
        result = None
        if kind == 'GetInputKindList':
            result = {'inputKinds': ['browser_source']}
        elif kind == 'GetVideoSettings':
            result = {'baseWidth': 1920, 'baseHeight': 1080}
        elif kind == 'GetInputSettings':
            source = self.sources[data['inputName']]
            result = {'inputKind': source['kind'], 'inputSettings': source['settings']}
        elif kind == 'CreateInput':
            self.sources[data['inputName']] = {'kind': data['inputKind'], 'settings': dict(data['inputSettings'])}
            self.items[(data['sceneName'], data['inputName'])] = 1
            result = {'sceneItemId': 1}
        elif kind == 'CreateSceneItem':
            self.items[(data['sceneName'], data['sourceName'])] = 2
            result = {'sceneItemId': 2}
        elif kind == 'SetInputSettings':
            self.sources[data['inputName']]['settings'].update(data['inputSettings'])
        elif kind == 'SetSceneItemTransform':
            self.transforms.append(data)
        return True, result, None, None

    async def test_create_reuse_and_empty_success_responses(self):
        first = await self.obs.ensure_browser_source('LIVE', 'TipTune Overlay', 'http://127.0.0.1:8765/overlay')
        self.assertTrue(first['configured'])
        self.assertTrue(first['created'])
        self.assertEqual(len(self.transforms), 1)
        again = await self.obs.ensure_browser_source('LIVE', 'TipTune Overlay', 'http://127.0.0.1:8765/overlay')
        self.assertTrue(again['configured'])
        self.assertFalse(again['created'])
        self.assertEqual(len(self.transforms), 1)
        self.assertEqual(len(self.sources), 1)

    async def test_existing_input_is_attached_without_duplicates(self):
        self.sources['TipTune Overlay'] = {'kind': 'browser_source', 'settings': {'url': 'http://localhost:8765/overlay'}}
        result = await self.obs.ensure_browser_source('LIVE', 'TipTune Overlay', 'http://127.0.0.1:9999/overlay')
        self.assertTrue(result['configured'])
        self.assertTrue(result['added_to_scene'])

    async def test_conflicts_and_missing_scene_do_not_mutate_sources(self):
        for kind, url in [('text_gdiplus_v3', ''), ('browser_source', 'https://example.org/')]:
            self.sources['TipTune Overlay'] = {'kind': kind, 'settings': {'url': url}}
            result = await self.obs.ensure_browser_source('LIVE', 'TipTune Overlay', 'http://127.0.0.1:8765/overlay')
            self.assertFalse(result['configured'])
            self.assertEqual(self.sources['TipTune Overlay']['settings']['url'], url)
        result = await self.obs.ensure_browser_source('', 'Other', 'http://127.0.0.1:8765/overlay')
        self.assertFalse(result['configured'])
        self.assertNotIn('Other', self.sources)

    async def test_failed_verification_is_reported(self):
        self.obs.get_browser_source_status = AsyncMock(return_value={'configured': False})
        result = await self.obs.ensure_browser_source('LIVE', 'TipTune Overlay', 'http://127.0.0.1:8765/overlay')
        self.assertFalse(result['configured'])
        self.assertIn('did not confirm', result['errors'][0])


class OverlayIntegrationTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.runtime = tempfile.TemporaryDirectory()
        root = Path(cls.runtime.name)
        cls.environment = patch.dict(os.environ, {'TIPTUNE_CONFIG': str(root / 'config.ini'),
            'TIPTUNE_CACHE_DIR': str(root / 'cache'), 'TIPTUNE_DEFAULT_LOG_PATH': str(root / 'test.log')})
        cls.environment.start()
        cls.app = importlib.import_module('app')

    @classmethod
    def tearDownClass(cls):
        cls.environment.stop()
        logging.shutdown()
        cls.runtime.cleanup()

    def setUp(self):
        self.storage = tempfile.TemporaryDirectory()
        self.addCleanup(self.storage.cleanup)
        self.root = Path(self.storage.name)
        self.cfg = config()
        self.cfg.read_dict({'OBS': {'enabled': 'false'}})
        for target, value in [('config', self.cfg), ('config_path', self.root / 'config.ini'),
                              ('get_cache_dir', Mock(return_value=self.root)),
                              ('Actions', Mock(return_value=SimpleNamespace(chatdj_enabled=False, obs_integration_enabled=False))),
                              ('_setup_logging', Mock())]:
            p = patch.object(self.app, target, value); p.start(); self.addCleanup(p.stop)
        p = patch('helpers.refresh_spotify_client'); p.start(); self.addCleanup(p.stop)
        self.service = self.app.SongRequestService()

    async def test_duplicate_requesters_survive_start_move_and_reload(self):
        self.service.actions.chatdj_enabled = True
        self.service.actions.auto_dj = SimpleNamespace(start_track=Mock(return_value=True))
        uri = 'spotify:track:0JvxCw2L2ChMeVpIfSwvqN'
        await self.service.add_track_to_queue({'source': 'spotify', 'uri': uri, 'requester': 'alice'})
        await self.service.add_track_to_queue({'source': 'spotify', 'uri': uri, 'requester': 'bob'})
        self.assertEqual(self.service._queue_now_playing['requester'], 'alice')
        self.assertEqual(self.service._queue_items[0]['requester'], 'bob')
        self.assertNotEqual(self.service._queue_items[0]['queue_entry_id'], self.service._queue_now_playing['queue_entry_id'])
        restored = self.app.SongRequestService()
        self.assertEqual(restored._queue_now_playing['requester'], 'alice')
        self.assertEqual(restored._queue_items[0]['queue_entry_id'], self.service._queue_items[0]['queue_entry_id'])

    async def test_read_apis_are_cached_and_overlay_bypasses_setup(self):
        self.service.get_queue_state = AsyncMock(side_effect=AssertionError('No per-client playback lookup'))
        self.service._fetch_spotify_track_meta = AsyncMock(side_effect=AssertionError('No per-client metadata lookup'))
        webui = self.app.WebUI(self.service)
        # Python tests run before Vite in CI and must not depend on local build output.
        overlay_html = '<!doctype html><html><body>overlay test document</body></html>'
        webui._overlay_index = self.root / 'overlay.html'
        webui._overlay_index.write_text(overlay_html, encoding='utf-8')
        client = TestClient(TestServer(webui._app)); await client.start_server()
        self.addAsyncCleanup(client.close)
        for _ in range(3):
            response = await client.get('/api/overlay/state')
            self.assertEqual(response.status, 200)
        response = await client.get('/overlay', allow_redirects=False)
        self.assertEqual(response.status, 200)
        self.assertEqual(await response.text(), overlay_html)
        self.assertEqual(response.content_type, 'text/html')
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        stream = await client.get('/api/overlay/events', headers={'Origin': 'http://tauri.localhost'})
        self.assertEqual(stream.headers['Access-Control-Allow-Origin'], 'http://tauri.localhost')
        self.assertEqual(await stream.content.readline(), b'event: snapshot\n')
        self.assertIn(b'schema_version', await stream.content.readline())
        self.assertEqual(len(self.service.overlay.subscribers), 1)
        self.service.overlay.alert('general', 'new state')
        self.service.get_queue_state.assert_not_awaited()
        self.service._fetch_spotify_track_meta.assert_not_awaited()
        stream.close()

    async def test_missing_overlay_build_returns_recovery_message_without_setup_redirect(self):
        webui = self.app.WebUI(self.service)
        webui._overlay_index = self.root / 'missing-overlay.html'
        async with TestClient(TestServer(webui._app)) as client:
            response = await client.get('/overlay', allow_redirects=False)
            self.assertEqual(response.status, 503)
            self.assertNotIn('Location', response.headers)
            self.assertEqual(await response.text(), 'Build the Web UI, then restart TipTune.')

    async def test_browser_tests_and_youtube_now_playing_work_without_obs(self):
        ok, error = await self.service.trigger_obs_test_overlay('WarningOverlay')
        self.assertTrue(ok, error)
        self.service._queue_now_playing = {'source': 'youtube', 'name': 'YouTube song', 'requester': 'alice'}
        ok, error = await self.service.trigger_obs_now_playing_overlay()
        self.assertTrue(ok, error)
        self.assertEqual(self.service.overlay.active[-1]['kind'], 'now_playing')

    async def test_invalid_config_does_not_write_and_valid_save_publishes(self):
        ok, _ = await self.service.update_config_from_ui({'Overlay': {'scale': '151'}})
        self.assertFalse(ok)
        self.assertFalse((self.root / 'config.ini').exists())
        subscriber = self.service.overlay.subscribe()
        ok, error = await self.service.update_config_from_ui({'Overlay': {'layout': 'compact', 'queue_length': '5'}})
        self.assertTrue(ok, error)
        self.assertEqual(subscriber.get_nowait()['config']['layout'], 'compact')

    async def test_failed_source_creation_does_not_switch_modes(self):
        self.cfg['OBS']['enabled'] = 'true'
        self.cfg['Overlay']['mode'] = 'text'
        self.service.overlay.configure(self.cfg)
        self.service._refresh_obs_integration_from_config = AsyncMock()
        self.service.actions.obs_integration_enabled = True
        self.service.actions.obs = SimpleNamespace(ensure_browser_source=AsyncMock(return_value={'configured': False, 'errors': ['conflict']}))
        self.service._web = SimpleNamespace(overlay_url='http://127.0.0.1:8765/overlay')
        result = await self.service.ensure_obs_browser_source({'scene_name': 'LIVE', 'activate': True})
        self.assertFalse(result['configured'])
        self.assertEqual(self.service.overlay.config['mode'], 'text')

    async def test_actual_runtime_port_is_used(self):
        webui = self.app.WebUI(self.service, port=0)
        await webui.start(); self.addAsyncCleanup(webui.stop)
        self.assertNotIn(':0/', webui.overlay_url)
        self.assertTrue(webui.overlay_url.endswith('/overlay'))

    async def test_busy_http_port_aborts_before_request_processing_starts(self):
        occupied = self.app.WebUI(self.service, port=0)
        await occupied.start()
        self.addAsyncCleanup(occupied.stop)
        with patch.dict(os.environ, {'TIPTUNE_WEB_HOST': '127.0.0.1', 'TIPTUNE_WEB_PORT': str(occupied._port)}):
            with self.assertRaises(OSError):
                await self.service.start()
        self.assertEqual(self.service._tasks, [])
        await self.service._web.stop()

    async def test_each_new_queue_entry_gets_an_independent_id(self):
        entry = {'source': 'spotify', 'uri': 'spotify:track:0JvxCw2L2ChMeVpIfSwvqN',
                 'queue_entry_id': 'copied-id', 'requester': 'alice'}
        first = self.service._normalize_queue_item(entry, 'spotify')
        second = self.service._normalize_queue_item(entry, 'spotify')
        self.assertNotEqual(first['queue_entry_id'], second['queue_entry_id'])
        self.assertEqual(first['requester'], 'alice')


if __name__ == '__main__':
    unittest.main()
