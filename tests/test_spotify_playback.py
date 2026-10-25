import asyncio
import json
from pathlib import Path
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from spotipy import SpotifyException

from app import SongRequestService
import app as application
from chatdj.chatdj import AutoDJ
from utils.error_reporting import ErrorReporter
from utils.support_diagnostics import current_support_id


URI = 'spotify:track:0JvxCw2L2ChMeVpIfSwvqN'


def playback(uri=URI, playing=True, device='desktop'):
    return {
        'is_playing': playing,
        'device': {'id': device},
        'item': {'uri': uri} if uri else None,
    }


def make_dj():
    dj = AutoDJ.__new__(AutoDJ)
    dj.spotify = Mock()
    dj.spotify.devices.return_value = {'devices': [{'id': 'desktop', 'name': 'Desktop'}]}
    dj.playback_device = 'desktop'
    dj._device_lock = threading.RLock()
    dj.now_playing_track_uri = None
    dj._last_start_playback_ts = 0
    dj._queue_lock = threading.Lock()
    dj.queued_tracks = []
    return dj


class StartTrackTests(unittest.TestCase):
    def setUp(self):
        self.dj = make_dj()
        self.sleep = patch('chatdj.chatdj.time.sleep').start()
        self.addCleanup(patch.stopall)

    def test_waits_for_device_to_load_the_track(self):
        self.dj.spotify.current_playback.side_effect = [playback(None, False), playback()]
        self.assertTrue(self.dj.start_track(URI))
        self.dj.spotify.start_playback.assert_called_once_with(device_id='desktop', uris=[URI])
        self.dj.spotify.pause_playback.assert_not_called()
        self.assertEqual(self.dj.now_playing_track_uri, URI)
        self.assertGreater(self.dj._last_start_playback_ts, 0)

    def test_accepted_command_without_playback_is_not_success(self):
        for state in (None, playback(None, False), playback(playing=False),
                      playback('spotify:track:other'), playback(device='speaker')):
            with self.subTest(state=state):
                self.dj.spotify.current_playback.return_value = state
                self.assertFalse(self.dj.start_track(URI))
                self.assertIsNone(self.dj.now_playing_track_uri)

    def test_relinked_track_is_confirmed(self):
        state = playback('spotify:track:replacement')
        state['item']['linked_from'] = {'uri': URI}
        self.dj.spotify.current_playback.return_value = state
        self.assertTrue(self.dj.start_track(URI))

    def test_expired_web_player_id_is_replaced_before_starting(self):
        self.dj.playback_device_name = 'Web Player'
        self.dj.spotify.devices.return_value = {'devices': [
            {'id': 'speaker', 'name': 'Speaker', 'is_active': True},
            {'id': 'new-web', 'name': 'Web Player'},
        ]}
        self.dj.spotify.current_playback.return_value = playback(device='new-web')
        self.assertTrue(self.dj.start_track(URI))
        self.dj.spotify.start_playback.assert_called_once_with(device_id='new-web', uris=[URI])

    def test_request_uses_embedded_player_when_it_appears_after_device_selection(self):
        self.dj.spotify.devices.return_value = {'devices': [
            {'id': 'desktop', 'name': 'Desktop', 'is_active': True},
            {'id': 'embedded', 'name': 'Web Player (Microsoft Edge)'},
        ]}
        self.dj.spotify.current_playback.return_value = playback(device='embedded')
        self.assertTrue(self.dj.start_track(URI))
        self.dj.spotify.start_playback.assert_called_once_with(device_id='embedded', uris=[URI])

    def test_request_preserves_manual_device_with_embedded_player_available(self):
        self.dj.playback_device_explicit = True
        self.dj.spotify.devices.return_value = {'devices': [
            {'id': 'embedded', 'name': 'Web Player (Microsoft Edge)'},
            {'id': 'desktop', 'name': 'Desktop'},
        ]}
        self.dj.spotify.current_playback.return_value = playback()
        self.assertTrue(self.dj.start_track(URI))
        self.dj.spotify.start_playback.assert_called_once_with(device_id='desktop', uris=[URI])

    def test_device_disappearing_after_lookup_is_refreshed_once(self):
        self.dj.playback_device_name = 'Web Player'
        self.dj.spotify.devices.side_effect = [
            {'devices': [{'id': 'desktop', 'name': 'Web Player'}]},
            {'devices': [{'id': 'new-web', 'name': 'Web Player'}]},
        ]
        self.dj.spotify.start_playback.side_effect = [SpotifyException(404, -1, 'Device not found'), None]
        self.dj.spotify.current_playback.return_value = playback(device='new-web')
        self.assertTrue(self.dj.start_track(URI))
        self.assertEqual([call.kwargs['device_id'] for call in self.dj.spotify.start_playback.call_args_list],
                         ['desktop', 'new-web'])

    def test_no_device_does_not_send_playback_to_an_unspecified_player(self):
        self.dj.spotify.devices.return_value = {'devices': []}
        with self.assertRaisesRegex(ValueError, 'Open Spotify'):
            self.dj.start_track(URI)
        self.dj.spotify.start_playback.assert_not_called()

    def test_404_without_a_replacement_does_not_repeat_playback(self):
        self.dj.spotify.start_playback.side_effect = SpotifyException(404, -1, 'Not found')
        with self.assertRaises(SpotifyException):
            self.dj.start_track(URI)
        self.dj.spotify.start_playback.assert_called_once()

    def test_clearing_idle_player_does_not_pause(self):
        self.dj.spotify.current_playback.return_value = playback(None, False)
        self.dj.spotify.queue.return_value = {'queue': []}
        self.assertTrue(self.dj.clear_playback_context(persist=False))
        self.dj.spotify.pause_playback.assert_not_called()

    def test_clearing_active_player_still_pauses(self):
        self.dj.spotify.current_playback.return_value = playback()
        self.dj.spotify.queue.return_value = {'queue': []}
        self.assertTrue(self.dj.clear_playback_context(persist=False))
        self.dj.spotify.pause_playback.assert_called_once_with(device_id='desktop')


class QueuePlaybackTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.dj = make_dj()
        self.service = SongRequestService.__new__(SongRequestService)
        self.service.actions = SimpleNamespace(chatdj_enabled=True, auto_dj=self.dj)
        self.service._queue_lock = asyncio.Lock()
        self.service._queue_items = [{'source': 'spotify', 'uri': URI}]
        self.service._queue_now_playing = None
        self.service._queue_paused = False
        self.service._queue_playback_paused = False
        self.service._queue_started_ts = None
        self.service._queue_starting = False
        self.service._queue_error = None
        self.service._active_source = Mock(return_value='spotify')
        self.service._persist_queue_state_to_disk = Mock()
        patch('chatdj.chatdj.time.sleep').start()
        self.addCleanup(patch.stopall)

    async def test_playback_failure_reports_the_original_request_id_and_updates_only_its_history(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        config_path = root / 'config.ini'
        config_path.write_text('[General]\nsend_error_reports=true')
        reporter = ErrorReporter(config_path, root / 'cache', 'https://public@example.org/1', start_worker=False)
        self.addCleanup(reporter.close)
        support_id = 'TT-' + 'a' * 32
        other_id = 'TT-' + 'b' * 32
        self.service._queue_items[0]['support_id'] = support_id
        self.service._request_history_recent = [
            {'status': 'added', 'support': {'support_id': support_id}},
            {'status': 'added', 'support': {'support_id': other_id}},
        ]
        self.service._persist_request_history_to_disk = Mock()

        def unconfirmed():
            self.assertEqual(current_support_id(), support_id)
            return playback(None, False)

        self.dj.spotify.current_playback.side_effect = unconfirmed
        with patch.object(application, 'error_reporter', reporter):
            self.assertFalse(await self.service._queue_start_next_if_needed())
        record, other = self.service._request_history_recent
        self.assertIn('did not start playback', record['playback_error'])
        self.assertNotIn('playback_error', other)
        events = [json.loads(path.read_text())['event'] for path in reporter.pending_dir.glob('*.json')]
        error = next(event for event in events if event.get('type') != 'transaction')
        self.assertEqual(error['tags']['error_code'], 'spotify.playback.unconfirmed')
        self.assertEqual(error['tags']['support_id'], support_id)
        self.assertEqual(error['event_id'], record['support']['report_id'])
        self.assertEqual(error['contexts']['diagnostics']['playback_backend'], 'spotify_external')
        self.assertNotIn(URI, json.dumps(error))

        self.dj.spotify.current_playback.side_effect = None
        self.dj.spotify.current_playback.return_value = playback()
        self.service._queue_paused = False
        with patch.object(application, 'error_reporter', reporter):
            self.assertTrue(await self.service._queue_start_next_if_needed())
        self.assertNotIn('playback_error', record)
        self.assertEqual(record['support']['report_id'], error['event_id'])

    async def test_disconnected_spotify_reports_unknown_backend_without_guessing_a_device(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        config_path = root / 'config.ini'
        config_path.write_text('[General]\nsend_error_reports=true')
        reporter = ErrorReporter(config_path, root / 'cache', 'https://public@example.org/1', start_worker=False)
        self.addCleanup(reporter.close)
        self.service.actions.chatdj_enabled = False
        with patch.object(application, 'error_reporter', reporter):
            self.assertFalse(await self.service._queue_start_next_if_needed())
        events = [json.loads(path.read_text())['event'] for path in reporter.pending_dir.glob('*.json')]
        error = next(event for event in events if event.get('type') != 'transaction')
        self.assertEqual(error['tags']['error_code'], 'spotify.playback.unavailable')
        self.assertEqual(error['contexts']['diagnostics']['playback_backend'], 'unknown')

    async def test_normal_shutdown_during_playback_start_does_not_create_a_failure_report(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        config_path = root / 'config.ini'
        config_path.write_text('[General]\nsend_error_reports=true')
        reporter = ErrorReporter(config_path, root / 'cache', 'https://public@example.org/1', start_worker=False)
        self.addCleanup(reporter.close)
        started = asyncio.Event()

        async def start_track(*args):
            started.set()
            await asyncio.Event().wait()

        with patch.object(application, 'error_reporter', reporter), patch('app.asyncio.to_thread', side_effect=start_track):
            task = asyncio.create_task(self.service._queue_start_next_if_needed())
            await asyncio.wait_for(started.wait(), 2)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        events = [json.loads(path.read_text())['event'] for path in reporter.pending_dir.glob('*.json')]
        self.assertFalse(any(event.get('type') != 'transaction' for event in events))
        self.assertEqual(len(self.service._queue_items), 1)

    async def test_resume_starts_pending_request_with_uri(self):
        self.dj.spotify.current_playback.return_value = playback()
        self.assertTrue(await self.service.resume_playback())
        self.dj.spotify.start_playback.assert_called_once_with(device_id='desktop', uris=[URI])
        self.assertEqual(self.service._queue_items, [])
        self.assertEqual(self.service._queue_now_playing['uri'], URI)

    async def test_unconfirmed_start_preserves_request(self):
        self.dj.spotify.current_playback.return_value = playback(None, False)
        self.assertFalse(await self.service._queue_start_next_if_needed())
        self.assertIsNone(self.service._queue_now_playing)
        self.assertIsNone(self.service._queue_started_ts)
        self.assertEqual([item['uri'] for item in self.service._queue_items], [URI])

    async def test_no_device_preserves_request_for_resume_after_spotify_opens(self):
        self.dj.spotify.devices.return_value = {'devices': []}
        self.assertFalse(await self.service._queue_start_next_if_needed())
        self.assertEqual([item['uri'] for item in self.service._queue_items], [URI])
        self.dj.spotify.start_playback.assert_not_called()
        self.dj.spotify.devices.return_value = {'devices': [{'id': 'new-web', 'name': 'Web Player'}]}
        self.dj.spotify.current_playback.return_value = playback(device='new-web')
        self.service.actions.trigger_queue_state_overlay = AsyncMock()
        self.assertTrue(await self.service.resume_queue())
        self.assertEqual(self.service._queue_items, [])
        self.assertEqual(self.service._queue_now_playing['uri'], URI)

    async def test_start_is_not_timed_as_playing_until_confirmed(self):
        def confirm():
            self.assertIsNone(self.service._queue_started_ts)
            self.assertIsNone(self.service._queue_now_playing)
            self.assertEqual([item['uri'] for item in self.service._queue_items], [URI])
            return playback()

        self.dj.spotify.current_playback.side_effect = confirm
        self.assertTrue(await self.service._queue_start_next_if_needed())
        self.assertIsNotNone(self.service._queue_started_ts)

    async def test_unconfirmed_start_stops_watchdog_retries(self):
        self.dj.spotify.current_playback.return_value = playback(None, False)
        self.assertFalse(await self.service._queue_start_next_if_needed())
        self.assertTrue(self.service._queue_paused)
        self.assertIn('did not start playback', self.service._queue_error)
        for _ in range(5):
            self.assertFalse(await self.service._queue_start_next_if_needed())
        self.dj.spotify.start_playback.assert_called_once()
        self.assertEqual(len(self.service._queue_items), 1)

    async def test_failure_is_visible_in_queue_api_state(self):
        self.dj.spotify.current_playback.return_value = playback(None, False)
        await self.service._queue_start_next_if_needed()
        self.service._enrich_mixed_queue_items = AsyncMock(side_effect=lambda items: items)
        state = await self.service.get_queue_state()
        self.assertTrue(state['paused'])
        self.assertFalse(state['starting'])
        self.assertIsNone(state['now_playing_item'])
        self.assertEqual(state['queued_tracks'], [URI])
        self.assertIn('Spotify Web Player', state['playback_error'])

    async def test_confirmation_preserves_duplicate_and_new_requests(self):
        original = self.service._queue_items[0]
        duplicate = dict(original)

        def confirm():
            self.service._queue_items.insert(0, duplicate)
            return playback()

        self.dj.spotify.current_playback.side_effect = confirm
        self.assertTrue(await self.service._queue_start_next_if_needed())
        self.assertEqual(len(self.service._queue_items), 1)
        self.assertIs(self.service._queue_items[0], duplicate)

    async def test_manual_resume_retries_once_and_clears_error_on_success(self):
        self.dj.spotify.current_playback.return_value = playback(None, False)
        await self.service._queue_start_next_if_needed()
        self.dj.spotify.current_playback.return_value = playback()
        self.service.actions.trigger_queue_state_overlay = AsyncMock()
        self.assertTrue(await self.service.resume_queue())
        self.assertFalse(self.service._queue_paused)
        self.assertIsNone(self.service._queue_error)
        self.assertEqual(self.dj.spotify.start_playback.call_count, 2)

    async def test_repeated_manual_failure_reports_failure_and_pauses_again(self):
        self.dj.spotify.current_playback.return_value = playback(None, False)
        await self.service._queue_start_next_if_needed()
        self.assertFalse(await self.service.resume_queue())
        self.assertTrue(self.service._queue_paused)
        self.assertEqual(len(self.service._queue_items), 1)

    async def test_concurrent_starts_do_not_send_duplicate_commands(self):
        entered = threading.Event()
        release = threading.Event()

        def confirm():
            entered.set()
            release.wait(timeout=3)
            return playback()

        self.dj.spotify.current_playback.side_effect = confirm
        first = asyncio.create_task(self.service._queue_start_next_if_needed())
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait, 2))
            self.assertFalse(await self.service._queue_start_next_if_needed())
            self.assertIsNone(self.service._queue_now_playing)
            self.assertEqual(len(self.service._queue_items), 1)
        finally:
            release.set()
        self.assertTrue(await first)
        self.dj.spotify.start_playback.assert_called_once()

    async def test_rejected_start_preserves_request(self):
        self.dj.spotify.start_playback.side_effect = SpotifyException(403, -1, 'Restriction violated')
        self.assertFalse(await self.service._queue_start_next_if_needed())
        self.assertIsNone(self.service._queue_now_playing)
        self.assertEqual([item['uri'] for item in self.service._queue_items], [URI])

    async def test_resume_retries_current_request_if_spotify_lost_context(self):
        self.service._queue_now_playing = self.service._queue_items.pop()
        self.dj.spotify.current_playback.side_effect = [playback(None, False), playback()]
        self.assertTrue(await self.service.resume_playback())
        self.dj.spotify.start_playback.assert_called_once_with(device_id='desktop', uris=[URI])

    async def test_existing_paused_track_resumes_without_restarting(self):
        self.service._queue_now_playing = self.service._queue_items.pop()
        self.service._queue_playback_paused = True
        self.dj.spotify.current_playback.return_value = playback(playing=False)
        self.assertTrue(await self.service.resume_playback())
        self.dj.spotify.start_playback.assert_called_once_with(device_id='desktop')
        self.assertFalse(self.service._queue_playback_paused)

    async def test_empty_idle_player_does_not_receive_bare_resume(self):
        self.service._queue_items.clear()
        self.dj.spotify.current_playback.return_value = playback(None, False)
        self.assertFalse(await self.service.resume_playback())
        self.dj.spotify.start_playback.assert_not_called()

    async def test_paused_queue_does_not_start_next_request(self):
        self.service._queue_paused = True
        self.assertFalse(await self.service.resume_playback())
        self.dj.spotify.start_playback.assert_not_called()


if __name__ == '__main__':
    unittest.main()
