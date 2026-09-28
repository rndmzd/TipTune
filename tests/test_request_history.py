import configparser
import importlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

from aiohttp.test_utils import TestClient, TestServer


class RequestHistoryTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        # Import without reading user credentials or writing user logs/caches.
        cls.runtime = tempfile.TemporaryDirectory()
        root = Path(cls.runtime.name)
        cls.environment = patch.dict(os.environ, {
            'TIPTUNE_CONFIG': str(root / 'config.ini'),
            'TIPTUNE_CACHE_DIR': str(root / 'cache'),
            'TIPTUNE_DEFAULT_LOG_PATH': str(root / 'test.log'),
        })
        cls.environment.start()
        cls.app = importlib.import_module('app')

    @classmethod
    def tearDownClass(cls):
        cls.environment.stop()
        # Close the isolated log before removing its directory on Windows.
        import logging
        logging.shutdown()
        cls.runtime.cleanup()

    def setUp(self):
        self.storage = tempfile.TemporaryDirectory()
        self.addCleanup(self.storage.cleanup)
        self.root = Path(self.storage.name)
        self.config = configparser.ConfigParser()
        for target, value in (
            ('config', self.config),
            ('config_path', self.root / 'config.ini'),
            ('get_cache_dir', Mock(return_value=self.root)),
            ('Actions', Mock(return_value=Mock(chatdj_enabled=False))),
            ('_setup_logging', Mock()),
        ):
            patcher = patch.object(self.app, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch('helpers.refresh_spotify_client')
        patcher.start()
        self.addCleanup(patcher.stop)

    def service(self):
        service = self.app.SongRequestService()
        service._refresh_obs_integration_from_config = AsyncMock()
        service._enrich_history_items = AsyncMock(side_effect=lambda items: items)
        return service

    async def test_default_retains_latest_1000_across_restart(self):
        (self.root / 'request_history.json').write_text(json.dumps([{'id': i} for i in range(1100)]))
        service = self.service()
        self.assertEqual(service._request_history_recent_max, 1000)
        service.publish_request_history_item({'id': 1100})
        restored = self.service()
        self.assertEqual(len(restored._request_history_recent), 1000)
        self.assertEqual(restored._request_history_recent[0]['id'], 101)
        self.assertEqual(restored._request_history_recent[-1]['id'], 1100)
        self.assertEqual(service.get_config_for_ui()['General']['request_history_size'], '1000')

    async def test_resize_applies_immediately_and_persists(self):
        service = self.service()
        service._request_history_recent = [{'id': i} for i in range(10)]
        self.assertEqual(await service.update_config_from_ui({'General': {'request_history_size': '3'}}), (True, None))
        self.assertEqual(json.loads((self.root / 'request_history.json').read_text()), [{'id': 7}, {'id': 8}, {'id': 9}])
        saved = configparser.ConfigParser()
        saved.read(self.root / 'config.ini')
        self.assertEqual(saved.getint('General', 'request_history_size'), 3)
        self.assertEqual(self.service()._request_history_recent_max, 3)
        self.assertEqual(await service.update_config_from_ui({'General': {'request_history_size': '1500'}}), (True, None))
        service.publish_request_history_item({'id': 10})
        self.assertEqual(len(service._request_history_recent), 4)
        self.assertEqual(service._request_history_recent_max, 1500)

    async def test_invalid_size_rejected_before_any_settings_are_written(self):
        service = self.service()
        for value in ('0', '-1', '1.5', '', 'abc', True):
            with self.subTest(value=value):
                ok, error = await service.update_config_from_ui({'General': {'song_cost': '99', 'request_history_size': value}})
                self.assertFalse(ok)
                self.assertIn('positive whole number', error)
                self.assertFalse((self.root / 'config.ini').exists())
                self.assertEqual(service._request_history_recent_max, 1000)

    async def test_invalid_ini_value_uses_default(self):
        for value in ('0', '-1', 'abc', '1.5'):
            with self.subTest(value=value):
                self.config.read_dict({'General': {'request_history_size': value}})
                self.assertEqual(self.service()._request_history_recent_max, 1000)

    async def test_api_returns_full_capacity_and_clamps_explicit_limit(self):
        self.config.read_dict({'General': {'request_history_size': '1500'}})
        service = self.service()
        service._request_history_recent = [{'id': i} for i in range(1500)]
        async with TestClient(TestServer(self.app.WebUI(service)._app)) as client:
            for query, count in (('', 1500), ('?limit=2000', 1500), ('?limit=700', 700), ('?limit=bad', 1500), ('?limit=0', 1)):
                with self.subTest(query=query):
                    response = await client.get('/api/history/recent' + query)
                    self.assertEqual(response.status, 200)
                    payload = await response.json()
                    self.assertEqual(payload['history_size'], 1500)
                    self.assertEqual(len(payload['history']), count)
                    self.assertEqual(payload['history'][-1]['id'], 1499)


if __name__ == '__main__':
    unittest.main()
