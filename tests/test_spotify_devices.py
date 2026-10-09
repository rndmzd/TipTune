import asyncio
import configparser
import json
import unittest
from unittest.mock import AsyncMock, Mock, patch

from requests.exceptions import ConnectionError, Timeout
from spotipy import SpotifyException
from spotipy.exceptions import SpotifyOauthError

import helpers
from app import SongRequestService, WebUI
from utils.spotify_errors import spotify_error_message


SCOPE = 'user-modify-playback-state user-read-playback-state user-read-currently-playing user-read-private'
INVALID_CLIENT = SpotifyOauthError('private provider payload', error='invalid_client')


class SpotifyInitializationTests(unittest.TestCase):
    def setUp(self):
        cfg = configparser.ConfigParser()
        cfg.read_dict({'Spotify': {'client_id': 'example-client', 'redirect_url': 'http://127.0.0.1:8888/callback'}})
        self.oauth = Mock()
        self.oauth.validate_token.return_value = {'scope': SCOPE}
        self.pkce = Mock(return_value=self.oauth)
        self.client = Mock()
        for name, value in [('config', cfg), ('SpotifyPKCE', self.pkce), ('Spotify', self.client),
                            ('spotify_client', None), ('spotify_client_error', None), ('sp_oauth', None)]:
            p = patch.object(helpers, name, value); p.start(); self.addCleanup(p.stop)

    def test_refresh_error_is_retained_with_actionable_message(self):
        self.oauth.validate_token.side_effect = INVALID_CLIENT
        helpers.refresh_spotify_client()
        self.assertIsNone(helpers.spotify_client)
        self.assertIn('invalid_client', helpers.spotify_client_error)
        self.assertIn('Developer Dashboard', helpers.spotify_client_error)
        self.assertNotIn('private provider payload', helpers.spotify_client_error)

    def test_successful_reconnect_clears_previous_error(self):
        helpers.spotify_client_error = 'previous failure'
        helpers.refresh_spotify_client()
        self.assertIs(helpers.spotify_client, self.client.return_value)
        self.assertIsNone(helpers.spotify_client_error)
        self.assertEqual(self.pkce.call_args.kwargs['requests_timeout'], 5)
        self.assertEqual(self.client.call_args.kwargs['requests_timeout'], 5)

    def test_missing_or_insufficient_authorization_has_reconnect_guidance(self):
        self.oauth.validate_token.return_value = None
        helpers.refresh_spotify_client()
        self.assertIsNone(helpers.spotify_client)
        self.assertIn('Connect Spotify in Settings', helpers.spotify_client_error)

    def test_removing_configuration_clears_old_client_and_error(self):
        helpers.config.remove_section('Spotify')
        helpers.spotify_client = Mock()
        helpers.spotify_client_error = 'old failure'
        helpers.refresh_spotify_client()
        self.assertIsNone(helpers.spotify_client)
        self.assertIsNone(helpers.spotify_client_error)


class SpotifyDeviceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.service = SongRequestService.__new__(SongRequestService)
        self.service._spotify_auth_lock = asyncio.Lock()
        self.service._spotify_auth_in_progress = False
        self.service._spotify_auth_url = None
        self.service._spotify_auth_error = None
        self.service._get_spotify_config_values = Mock(return_value=('client', 'http://127.0.0.1:8888/callback'))
        self.oauth = Mock(scope=SCOPE)
        self.oauth.cache_handler.get_cached_token.return_value = {'scope': SCOPE}
        self.service._build_spotify_oauth = Mock(return_value=self.oauth)

    async def test_cached_token_does_not_claim_authorization_after_refresh_failure(self):
        with patch.object(helpers, 'spotify_client', None), patch.object(helpers, 'spotify_client_error', spotify_error_message(INVALID_CLIENT)):
            status = await self.service.get_spotify_auth_status()
        self.assertFalse(status['authorized'])
        self.assertFalse(status['client_ready'])
        self.assertIn('invalid_client', status['error'])
        self.oauth.validate_token.assert_not_called()

    async def test_status_uses_cached_scopes_without_network_refresh(self):
        with patch.object(helpers, 'spotify_client', Mock()), patch.object(helpers, 'spotify_client_error', None):
            self.assertTrue((await self.service.get_spotify_auth_status())['authorized'])
            self.oauth.cache_handler.get_cached_token.return_value = {'scope': 'user-read-private'}
            self.assertFalse((await self.service.get_spotify_auth_status())['authorized'])
        self.oauth.validate_token.assert_not_called()

    async def test_initialization_failure_reaches_device_endpoint(self):
        with patch.object(helpers, 'spotify_client', None), patch.object(helpers, 'spotify_client_error', spotify_error_message(INVALID_CLIENT)), patch.object(helpers, 'refresh_spotify_client'):
            devices, error = await self.service.get_spotify_devices()
        self.assertEqual(devices, [])
        self.assertIn('invalid_client', error)

    async def test_api_error_is_distinct_from_successful_empty_result(self):
        client = Mock()
        with patch.object(helpers, 'spotify_client', client):
            client.devices.return_value = {'devices': []}
            self.assertEqual(await self.service.get_spotify_devices(), ([], None))
            client.devices.side_effect = SpotifyException(403, -1, 'secret payload')
            devices, error = await self.service.get_spotify_devices()
        self.assertEqual(devices, [])
        self.assertIn('denied API access', error)
        self.assertNotIn('secret', error)

    async def test_devices_appear_after_client_is_reinitialized(self):
        client = Mock()
        client.devices.return_value = {'devices': [{'id': 'web', 'name': 'Web Player', 'is_active': True}]}
        def refresh():
            helpers.spotify_client = client
        with patch.object(helpers, 'spotify_client', None), patch.object(helpers, 'refresh_spotify_client', side_effect=refresh):
            devices, error = await self.service.get_spotify_devices()
        self.assertIsNone(error)
        self.assertEqual(devices[0]['name'], 'Web Player')
        client.devices.assert_called_once()

    async def test_mid_session_authorization_failure_is_visible_until_recovery(self):
        client = Mock()
        client.devices.side_effect = INVALID_CLIENT
        with patch.object(helpers, 'spotify_client', client), patch.object(helpers, 'spotify_client_error', None):
            await self.service.get_spotify_devices()
            status = await self.service.get_spotify_auth_status()
            self.assertFalse(status['authorized'])
            self.assertIn('invalid_client', status['error'])
            client.devices.side_effect = None
            client.devices.return_value = {'devices': []}
            await self.service.get_spotify_devices()
            self.assertTrue((await self.service.get_spotify_auth_status())['authorized'])
            self.assertIsNone(helpers.spotify_client_error)

    async def test_endpoint_marks_errors_as_failure_and_empty_success_as_success(self):
        server = WebUI.__new__(WebUI)
        server._service = Mock()
        server._service.get_spotify_devices = AsyncMock(return_value=([], 'Reconnect Spotify in Settings.'))
        payload = json.loads((await server._api_devices(None)).text)
        self.assertFalse(payload['ok'])
        self.assertIn('Reconnect', payload['error'])
        server._service.get_spotify_devices.return_value = ([], None)
        self.assertEqual(json.loads((await server._api_devices(None)).text), {'ok': True, 'devices': []})


class SpotifyErrorTests(unittest.TestCase):
    def test_errors_offer_recovery_without_raw_provider_payloads(self):
        for exc, text in [(INVALID_CLIENT, 'Client ID'),
                          (SpotifyOauthError('secret', error='invalid_grant'), 'Reconnect'),
                          (SpotifyException(401, -1, 'secret'), 'Reconnect'),
                          (SpotifyException(429, -1, 'secret'), 'rate limiting'),
                          (Timeout('secret'), 'respond in time'),
                          (ConnectionError('secret'), 'reach Spotify'),
                          (ValueError('secret'), 'reconnect')]:
            with self.subTest(exception=type(exc).__name__, text=text):
                message = spotify_error_message(exc)
                self.assertIn(text, message)
                self.assertNotIn('secret', message)


if __name__ == '__main__':
    unittest.main()
