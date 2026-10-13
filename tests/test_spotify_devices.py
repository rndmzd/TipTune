import asyncio
import configparser
import json
import unittest
from unittest.mock import AsyncMock, Mock, patch
from types import SimpleNamespace

from requests.exceptions import ConnectionError, Timeout
from spotipy import SpotifyException
from spotipy.exceptions import SpotifyOauthError

import helpers
from app import SongRequestService, WebUI
from chatdj.chatdj import AutoDJ
from utils.spotify_errors import spotify_error_message


SCOPE = 'user-modify-playback-state user-read-playback-state user-read-currently-playing user-read-private'
INVALID_CLIENT = SpotifyOauthError('private provider payload', error='invalid_client')


class PlaybackDeviceStartupTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock()
        for name in ('clear_playback_context', '_restore_persisted_queue_state'):
            p = patch.object(AutoDJ, name); p.start(); self.addCleanup(p.stop)
        p = patch('builtins.input', side_effect=AssertionError('Startup must never prompt'))
        self.prompt = p.start(); self.addCleanup(p.stop)

    def test_no_devices_allows_startup_without_console_or_transfer(self):
        self.client.devices.return_value = {'devices': []}
        dj = AutoDJ(self.client, playback_device_id='expired-web')
        self.assertIsNone(dj.playback_device)
        self.assertEqual(dj.queued_tracks, [])
        self.assertFalse(dj.queue_paused())
        self.prompt.assert_not_called()
        self.client.transfer_playback.assert_not_called()
        self.client.devices.assert_called_once()

    def test_failed_device_lookup_allows_startup(self):
        self.client.devices.side_effect = Timeout('Spotify is temporarily unreachable')
        self.assertIsNone(AutoDJ(self.client).playback_device)
        self.prompt.assert_not_called()

    def test_stale_saved_id_selects_current_active_device_without_transfer(self):
        self.client.devices.return_value = {'devices': [
            {'id': 'other', 'name': 'Speaker'},
            {'id': 'new-web', 'name': 'Web Player', 'is_active': True},
        ]}
        dj = AutoDJ(self.client, playback_device_id='expired-web')
        self.assertEqual(dj.playback_device, 'new-web')
        self.assertEqual(dj.playback_device_name, 'Web Player')
        self.client.transfer_playback.assert_not_called()

    def test_valid_saved_device_is_preserved_over_another_active_player(self):
        self.client.devices.return_value = {'devices': [
            {'id': 'other', 'name': 'Speaker', 'is_active': True},
            {'id': 'saved', 'name': 'Web Player'},
        ]}
        self.assertEqual(AutoDJ(self.client, playback_device_id='saved').playback_device, 'saved')

    def test_embedded_player_is_default_over_saved_and_active_devices(self):
        for name in ('Web Player (Microsoft Edge)', ' web PLAYER (MICROSOFT EDGE) '):
            with self.subTest(name=name):
                self.client.devices.return_value = {'devices': [
                    {'id': 'saved', 'name': 'Desktop', 'is_active': True},
                    {'id': 'chrome', 'name': 'Web Player (Chrome)'},
                    {'id': 'embedded', 'name': name},
                ]}
                dj = AutoDJ(self.client, playback_device_id='saved')
                self.assertEqual(dj.playback_device, 'embedded')
                self.assertFalse(dj.playback_device_explicit)
        self.client.transfer_playback.assert_not_called()

    def test_embedded_player_becomes_default_when_it_appears_after_startup(self):
        self.client.devices.return_value = {'devices': [
            {'id': 'speaker', 'name': 'Speaker', 'is_active': True},
        ]}
        dj = AutoDJ(self.client)
        self.assertEqual(dj.playback_device, 'speaker')
        self.client.devices.return_value['devices'].append(
            {'id': 'embedded', 'name': 'Web Player (Microsoft Edge)'}
        )
        self.assertTrue(dj._refresh_playback_device())
        self.assertEqual(dj.playback_device, 'embedded')
        self.client.transfer_playback.assert_not_called()

    def test_unusable_embedded_players_do_not_override_saved_device(self):
        self.client.devices.return_value = {'devices': [
            {'id': 'restricted', 'name': 'Web Player (Microsoft Edge)', 'is_restricted': True},
            {'id': None, 'name': 'Web Player (Microsoft Edge)', 'is_active': True},
            {'id': 'saved', 'name': 'Desktop'},
        ]}
        self.assertEqual(AutoDJ(self.client, playback_device_id='saved').playback_device, 'saved')

    def test_manual_selection_is_preserved_over_embedded_default(self):
        self.client.devices.return_value = {'devices': [
            {'id': 'speaker', 'name': 'Speaker', 'is_active': True},
            {'id': 'embedded', 'name': 'Web Player (Microsoft Edge)'},
        ]}
        dj = AutoDJ(self.client)
        self.assertEqual(dj.playback_device, 'embedded')
        self.assertTrue(dj.set_playback_device('speaker'))
        self.assertTrue(dj._refresh_playback_device())
        self.assertEqual(dj.playback_device, 'speaker')
        self.assertTrue(dj.playback_device_explicit)
        self.client.transfer_playback.assert_called_once_with(device_id='speaker', force_play=False)

    def test_failed_manual_selection_does_not_disable_embedded_default(self):
        self.client.devices.return_value = {'devices': [
            {'id': 'embedded', 'name': 'Web Player (Microsoft Edge)'},
        ]}
        dj = AutoDJ(self.client)
        self.client.transfer_playback.side_effect = SpotifyException(404, -1, 'Device not found')
        self.assertFalse(dj.set_playback_device('missing'))
        self.assertFalse(dj.playback_device_explicit)
        self.assertTrue(dj._refresh_playback_device())
        self.assertEqual(dj.playback_device, 'embedded')

    def test_missing_manual_device_falls_back_to_embedded_default(self):
        self.client.devices.return_value = {'devices': [
            {'id': 'speaker', 'name': 'Speaker'},
            {'id': 'embedded', 'name': 'Web Player (Microsoft Edge)'},
        ]}
        dj = AutoDJ(self.client)
        self.assertTrue(dj.set_playback_device('speaker'))
        self.client.devices.return_value = {'devices': [
            {'id': 'desktop', 'name': 'Desktop', 'is_active': True},
            {'id': 'embedded', 'name': 'Web Player (Microsoft Edge)'},
        ]}
        self.assertTrue(dj._refresh_playback_device())
        self.assertEqual(dj.playback_device, 'embedded')
        self.assertFalse(dj.playback_device_explicit)

    def test_restricted_and_missing_device_ids_are_skipped(self):
        self.client.devices.return_value = {'devices': [
            {'id': None, 'is_active': True},
            {'id': 'restricted', 'is_restricted': True, 'is_active': True},
            {'id': 'usable', 'name': 'Web Player'},
        ]}
        self.assertEqual(AutoDJ(self.client, playback_device_id='restricted').playback_device, 'usable')

    def test_background_activation_transfers_to_default_without_forcing_play(self):
        self.client.devices.return_value = {'devices': [
            {'id': 'desktop', 'name': 'Desktop', 'is_active': True},
            {'id': 'embedded', 'name': 'Web Player (Microsoft Edge)'},
        ]}
        self.client.transfer_playback.return_value = None  # Spotify's successful 204 response.
        dj = AutoDJ(self.client, playback_device_id='desktop')
        dj.queued_tracks = ['pending-request']
        self.assertTrue(dj.ensure_playback_device_active())
        self.assertEqual(dj.playback_device, 'embedded')
        self.assertFalse(dj.playback_device_explicit)
        self.assertEqual(dj.queued_tracks, ['pending-request'])
        self.client.transfer_playback.assert_called_once_with(device_id='embedded', force_play=False)
        self.client.start_playback.assert_not_called()
        self.client.pause_playback.assert_not_called()

    def test_active_default_does_not_need_a_transfer(self):
        self.client.devices.return_value = {'devices': [
            {'id': 'embedded', 'name': 'Web Player (Microsoft Edge)', 'is_active': True},
        ]}
        self.assertTrue(AutoDJ(self.client).ensure_playback_device_active())
        self.client.transfer_playback.assert_not_called()

    def test_delayed_transfer_confirmation_does_not_repeat_every_poll(self):
        self.client.devices.return_value = {'devices': [
            {'id': 'embedded', 'name': 'Web Player (Microsoft Edge)'},
        ]}
        dj = AutoDJ(self.client)
        with patch('chatdj.chatdj.time.monotonic', side_effect=[100, 105, 130]):
            self.assertTrue(dj.ensure_playback_device_active())
            self.assertFalse(dj.ensure_playback_device_active())
            self.assertTrue(dj.ensure_playback_device_active())
        self.assertEqual(self.client.transfer_playback.call_count, 2)

    def test_failed_activation_retains_queue_and_retries_after_cooldown(self):
        self.client.devices.return_value = {'devices': [
            {'id': 'embedded', 'name': 'Web Player (Microsoft Edge)'},
        ]}
        self.client.transfer_playback.side_effect = [Timeout('Temporarily offline'), None]
        dj = AutoDJ(self.client)
        dj.queued_tracks = ['pending-request']
        with patch('chatdj.chatdj.time.monotonic', side_effect=[100, 105, 130]):
            self.assertFalse(dj.ensure_playback_device_active())
            self.assertFalse(dj.ensure_playback_device_active())
            self.assertTrue(dj.ensure_playback_device_active())
        self.assertEqual(dj.playback_device, 'embedded')
        self.assertEqual(dj.queued_tracks, ['pending-request'])
        self.assertFalse(dj.queue_paused())
        self.assertEqual(self.client.transfer_playback.call_count, 2)

    def test_reconnected_device_id_does_not_wait_for_old_id_retry(self):
        self.client.devices.return_value = {'devices': [
            {'id': 'old-web', 'name': 'Web Player (Microsoft Edge)'},
        ]}
        self.client.transfer_playback.side_effect = [SpotifyException(404, -1, 'Device not found'), None]
        dj = AutoDJ(self.client)
        with patch('chatdj.chatdj.time.monotonic', side_effect=[100, 105]):
            self.assertFalse(dj.ensure_playback_device_active())
            self.client.devices.return_value = {'devices': [
                {'id': 'new-web', 'name': 'Web Player (Microsoft Edge)'},
            ]}
            self.assertTrue(dj.ensure_playback_device_active())
        self.assertEqual([call.kwargs['device_id'] for call in self.client.transfer_playback.call_args_list],
                         ['old-web', 'new-web'])

    def test_background_activation_waits_for_player_to_appear(self):
        self.client.devices.return_value = {'devices': []}
        dj = AutoDJ(self.client)
        self.assertFalse(dj.ensure_playback_device_active())
        self.client.transfer_playback.assert_not_called()
        self.client.devices.return_value = {'devices': [
            {'id': 'embedded', 'name': 'Web Player (Microsoft Edge)'},
        ]}
        self.assertTrue(dj.ensure_playback_device_active())
        self.client.transfer_playback.assert_called_once_with(device_id='embedded', force_play=False)

    def test_background_activation_respects_manual_selection(self):
        self.client.devices.return_value = {'devices': [
            {'id': 'speaker', 'name': 'Speaker'},
            {'id': 'embedded', 'name': 'Web Player (Microsoft Edge)', 'is_active': True},
        ]}
        dj = AutoDJ(self.client)
        self.assertTrue(dj.set_playback_device('speaker'))
        self.client.transfer_playback.reset_mock()
        self.assertTrue(dj.ensure_playback_device_active())
        self.assertEqual(dj.playback_device, 'speaker')
        self.assertTrue(dj.playback_device_explicit)
        self.client.transfer_playback.assert_called_once_with(device_id='speaker', force_play=False)

    def test_background_activation_skips_unusable_embedded_players(self):
        self.client.devices.return_value = {'devices': [
            {'id': 'restricted', 'name': 'Web Player (Microsoft Edge)', 'is_restricted': True},
            {'id': None, 'name': 'Web Player (Microsoft Edge)'},
            {'id': 'speaker', 'name': 'Speaker', 'is_active': True},
        ]}
        dj = AutoDJ(self.client)
        self.assertTrue(dj.ensure_playback_device_active())
        self.assertEqual(dj.playback_device, 'speaker')
        self.client.transfer_playback.assert_not_called()

    def test_confirmed_activation_can_recover_from_another_device_switch(self):
        embedded = {'id': 'embedded', 'name': 'Web Player (Microsoft Edge)'}
        self.client.devices.return_value = {'devices': [embedded]}
        dj = AutoDJ(self.client)
        with patch('chatdj.chatdj.time.monotonic', side_effect=[100, 110]):
            self.assertTrue(dj.ensure_playback_device_active())
            embedded['is_active'] = True
            self.assertTrue(dj.ensure_playback_device_active())
            embedded['is_active'] = False
            self.assertTrue(dj.ensure_playback_device_active())
        self.assertEqual(self.client.transfer_playback.call_count, 2)


class SpotifyDeviceWatchdogTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        for name in ('clear_playback_context', '_restore_persisted_queue_state'):
            p = patch.object(AutoDJ, name); p.start(); self.addCleanup(p.stop)
        self.client = Mock()
        self.client.devices.return_value = {'devices': [
            {'id': 'desktop', 'name': 'Desktop', 'is_active': True},
        ]}
        self.dj = AutoDJ(self.client)
        self.dj.check_queue_status = Mock(return_value=False)
        self.service = SongRequestService.__new__(SongRequestService)
        self.service.actions = SimpleNamespace(chatdj_enabled=True, auto_dj=self.dj)
        self.service._active_source = Mock(return_value='spotify')
        self.service._queue_lock = asyncio.Lock()
        self.service._queue_items = []
        self.service._queue_paused = True
        self.service._queue_playback_paused = True
        self.service._queue_now_playing = None
        self.service._queue_started_ts = None
        self.service._queue_starting = False
        self.service._queue_start_next_if_needed = AsyncMock()
        self.service._stop_event = Mock(is_set=Mock(side_effect=[False, False, True]), wait=AsyncMock())

    async def test_watchdog_activates_late_player_with_empty_paused_queue(self):
        self.client.devices.side_effect = [
            {'devices': [{'id': 'desktop', 'name': 'Desktop', 'is_active': True}]},
            {'devices': [
                {'id': 'desktop', 'name': 'Desktop', 'is_active': True},
                {'id': 'embedded', 'name': 'Web Player (Microsoft Edge)'},
            ]},
        ]
        await self.service._queue_watchdog()
        self.assertEqual(self.dj.playback_device, 'embedded')
        self.client.transfer_playback.assert_called_once_with(device_id='embedded', force_play=False)
        self.assertEqual(self.service._queue_items, [])
        self.assertTrue(self.service._queue_paused)
        self.assertIsNone(self.service._queue_now_playing)
        self.assertEqual(self.client.devices.call_count, 3)  # Startup, then both background polls.

    async def test_watchdog_activates_available_player_without_settings(self):
        self.client.devices.return_value = {'devices': [
            {'id': 'desktop', 'name': 'Desktop', 'is_active': True},
            {'id': 'embedded', 'name': 'Web Player (Microsoft Edge)'},
        ]}
        await self.service._queue_watchdog()
        self.client.transfer_playback.assert_called_once_with(device_id='embedded', force_play=False)
        self.assertFalse(self.dj.playback_device_explicit)

    async def test_watchdog_defers_activation_during_youtube_or_track_start(self):
        for source, now_item, starting in [
            ('youtube', None, False),
            ('spotify', {'source': 'youtube'}, False),
            ('spotify', None, True),
        ]:
            with self.subTest(source=source, now_item=now_item, starting=starting):
                self.service._active_source.return_value = source
                self.service._queue_now_playing = now_item
                self.service._queue_starting = starting
                self.service._stop_event.is_set.side_effect = [False, True]
                self.client.reset_mock()
                await self.service._queue_watchdog()
                self.client.devices.assert_not_called()
                self.client.transfer_playback.assert_not_called()


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

    async def test_spotify_204_transfer_is_successfully_saved_without_chatdj(self):
        self.service.actions = SimpleNamespace(chatdj_enabled=False)
        self.service.update_config_from_ui = AsyncMock(return_value=(True, None))
        client = Mock()
        client.transfer_playback.return_value = None
        with patch.object(helpers, 'spotify_client', client):
            self.assertTrue(await self.service.set_spotify_device('new-web'))
        client.transfer_playback.assert_called_once_with('new-web', False)
        self.service.update_config_from_ui.assert_awaited_once_with({'Spotify': {'playback_device_id': 'new-web'}})

    async def test_failed_transfer_does_not_save_device(self):
        self.service.actions = SimpleNamespace(chatdj_enabled=False)
        self.service.update_config_from_ui = AsyncMock()
        client = Mock()
        client.transfer_playback.side_effect = SpotifyException(404, -1, 'Device not found')
        with patch.object(helpers, 'spotify_client', client):
            self.assertFalse(await self.service.set_spotify_device('expired-web'))
        self.service.update_config_from_ui.assert_not_awaited()

    async def test_device_save_failure_is_reported_for_both_runtime_paths(self):
        for enabled in (False, True):
            with self.subTest(chatdj_enabled=enabled):
                self.service.actions = SimpleNamespace(chatdj_enabled=enabled,
                    auto_dj=Mock(set_playback_device=Mock(return_value=True)))
                self.service.update_config_from_ui = AsyncMock(return_value=(False, 'Disk full'))
                with patch.object(helpers, 'spotify_client', Mock()):
                    self.assertFalse(await self.service.set_spotify_device('new-web'))


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
