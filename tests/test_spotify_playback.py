import asyncio
import json
from dataclasses import replace
from pathlib import Path
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import httpx
from spotipy import SpotifyException

from app import SongRequestService
import app as application
from chatdj.chatdj import AutoDJ
from helpers.actions import Actions
from utils.error_reporting import ErrorReporter
from utils.support_diagnostics import current_support_id


URI = 'spotify:track:0JvxCw2L2ChMeVpIfSwvqN'
NEXT_URI = 'spotify:track:next'
OTHER_URI = 'spotify:track:other'


def playback(uri=URI, playing=True, device='desktop', *, progress_ms=None, duration_ms=None, timestamp=None):
    return {
        'is_playing': playing,
        'device': {'id': device},
        'item': {'uri': uri, 'duration_ms': duration_ms} if uri else None,
        'progress_ms': progress_ms,
        'timestamp': timestamp,
    }


def recording(uri=URI, *, isrc='USRC11000915', artist='artist', duration=240000):
    return {
        'uri': uri,
        'external_ids': {'isrc': isrc},
        'artists': [{'id': artist}],
        'duration_ms': duration,
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

    def test_equivalent_recording_without_linked_from_is_confirmed(self):
        state = playback('spotify:track:alternate-release')
        state['item'] = recording(state['item']['uri'], duration=241053)
        self.dj.spotify.track.return_value = recording()
        self.dj.spotify.current_playback.return_value = state

        self.assertTrue(self.dj.start_track(URI))
        self.assertEqual(self.dj.now_playing_track_uri, state['item']['uri'])
        self.dj.spotify.track.assert_called_once_with(URI)
        self.dj.spotify.start_playback.assert_called_once()

    def test_different_recording_is_not_confirmed_despite_matching_title(self):
        for changes in ({'isrc': 'USUM71509342'}, {'artist': 'other'},
                        {'duration': 260000}, {'isrc': ''}):
            with self.subTest(changes=changes):
                self.dj.spotify.track.reset_mock()
                state = playback('spotify:track:other')
                state['item'] = dict(recording(state['item']['uri'], **changes), name='Same title')
                self.dj.spotify.track.return_value = dict(recording(), name='Same title')
                self.dj.spotify.current_playback.return_value = state

                self.assertFalse(self.dj.start_track(URI))
                self.assertLessEqual(self.dj.spotify.track.call_count, 1)

    def test_recording_match_on_another_device_is_not_confirmed(self):
        state = playback('spotify:track:alternate-release', device='speaker')
        state['item'] = recording(state['item']['uri'])
        self.dj.spotify.track.return_value = recording()
        self.dj.spotify.current_playback.return_value = state

        self.assertFalse(self.dj.start_track(URI))
        self.dj.spotify.track.assert_not_called()

    def test_failed_recording_lookup_keeps_the_request_unconfirmed(self):
        state = playback('spotify:track:alternate-release')
        state['item'] = recording(state['item']['uri'])
        self.dj.spotify.track.side_effect = SpotifyException(429, -1, 'Rate limited')
        self.dj.spotify.current_playback.return_value = state

        self.assertFalse(self.dj.start_track(URI))
        self.dj.spotify.track.assert_called_once_with(URI)

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
        self.service.actions.skip_song = Actions.skip_song.__get__(self.service.actions)
        self.service.actions.trigger_queue_state_overlay = AsyncMock()
        self.service._queue_lock = asyncio.Lock()
        self.service._queue_items = [{'source': 'spotify', 'uri': URI, 'queue_entry_id': 'first'}]
        self.service._queue_now_playing = None
        self.service._queue_paused = False
        self.service._queue_playback_paused = False
        self.service._queue_started_ts = None
        self.service._queue_starting = False
        self.service._queue_error = None
        self.service._queue_spotify_observation = None
        self.service._queue_spotify_transition = None
        self.service._queue_spotify_attempt = None
        self.service._queue_last_spotify_uris = ()
        self.service._active_source = Mock(return_value='spotify')
        self.service._persist_queue_state_to_disk = Mock()
        patch('chatdj.chatdj.time.sleep').start()
        self.addCleanup(patch.stopall)

    async def test_playback_failure_reports_the_original_request_id_and_updates_only_its_history(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        config_path = root / 'config.ini'
        config_path.write_text('[General]\nsend_error_reports=true')
        reporter = ErrorReporter(config_path, root / 'cache', 'https://public@example.org/1',
                                 start_worker=False, traces_sample_rate=0)
        self.addCleanup(reporter.close)
        support_id = 'TT-' + 'a' * 32
        other_id = 'TT-' + 'b' * 32
        request = self.service._queue_items[0]
        request['support_id'] = support_id
        self.service._request_history_recent = [
            {'status': 'added', 'support': {'support_id': support_id}},
            {'status': 'added', 'support': {'support_id': other_id}},
        ]
        self.service._persist_request_history_to_disk = Mock()

        other_device = playback(device='private-device-id')
        other_device['device']['name'] = 'private-device-name'
        other_track = playback('spotify:track:private-track-id')
        other_track['item']['name'] = 'private-song-name'
        states = iter([None, {}, playback(None, False), playback(playing=False), other_device] + [other_track] * 6)

        def unconfirmed():
            self.assertEqual(current_support_id(), support_id)
            return next(states)

        self.dj.spotify.current_playback.side_effect = unconfirmed
        with patch.object(application, 'error_reporter', reporter):
            self.assertFalse(await self.service._queue_start_next_if_needed())
        self.dj.spotify.start_playback.assert_called_once_with(device_id='desktop', uris=[URI])
        self.assertEqual(self.dj.spotify.current_playback.call_count, 11)
        self.assertTrue(self.service._queue_paused)
        self.assertIsNone(self.service._queue_now_playing)
        self.assertEqual(self.service._queue_items, [request])
        record, other = self.service._request_history_recent
        self.assertIn('did not start playback', record['playback_error'])
        self.assertNotIn('playback_error', other)
        events = [json.loads(path.read_text())['event'] for path in reporter.pending_dir.glob('*.json')]
        error = next(event for event in events if event.get('type') != 'transaction')
        self.assertEqual(error['tags']['error_code'], 'spotify.playback.unconfirmed')
        self.assertEqual(error['tags']['support_id'], support_id)
        self.assertEqual(error['event_id'], record['support']['report_id'])
        self.assertEqual(error['contexts']['diagnostics']['playback_backend'], 'spotify_external')
        self.assertEqual(error['contexts']['failure']['playback_confirmation'], {
            'no_playback_state': 2, 'not_playing': 2, 'device_mismatch': 1, 'track_mismatch': 6,
        })

        with patch('utils.error_reporting.httpx.post', return_value=httpx.Response(200)) as post:
            reporter.process_pending()
        post.assert_called_once()
        uploaded = json.loads(post.call_args.kwargs['content'].split(b'\n', 2)[2])
        self.assertEqual(uploaded['tags']['support_id'], support_id)
        self.assertEqual(uploaded['contexts']['failure'], error['contexts']['failure'])
        serialized = json.dumps([error, uploaded])
        for private_value in (URI.removeprefix('spotify:track:'), 'private-track-id', 'private-song-name',
                              'private-device-id', 'private-device-name'):
            self.assertNotIn(private_value, serialized)

        self.dj.spotify.current_playback.side_effect = [playback(None, False), playback(progress_ms=100)]
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

        async def start_track(*args, **kwargs):
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
        self.dj.spotify.current_playback.side_effect = [playback(None, False), playback(None, False), playback(progress_ms=100)]
        self.service.actions.trigger_queue_state_overlay = AsyncMock()
        self.assertTrue(await self.service.resume_queue())
        self.assertFalse(self.service._queue_paused)
        self.assertIsNone(self.service._queue_error)
        self.assertEqual(self.dj.spotify.start_playback.call_count, 2)

    async def test_resume_recovers_an_equivalent_recording_and_tracks_its_playing_uri(self):
        self.dj.spotify.current_playback.return_value = playback(None, False)
        await self.service._queue_start_next_if_needed()
        state = playback('spotify:track:alternate-release')
        state['item'] = recording(state['item']['uri'], duration=241053)
        state['progress_ms'] = 1200
        state['timestamp'] = self.service._queue_spotify_attempt[2].sent_ms
        self.dj.spotify.track.return_value = recording()
        self.dj.spotify.current_playback.return_value = state
        self.service.actions.trigger_queue_state_overlay = AsyncMock()
        self.service._enrich_mixed_queue_items = AsyncMock(side_effect=lambda items: items)

        self.assertTrue(await self.service.resume_queue())
        self.assertFalse(self.service._queue_paused)
        self.assertIsNone(self.service._queue_error)
        self.assertEqual(self.service._queue_items, [])
        self.assertEqual(self.service._queue_now_playing['uri'], state['item']['uri'])
        self.assertEqual(self.service._queue_now_playing['requested_uri'], URI)
        queue = await self.service.get_queue_state()
        self.assertTrue(queue['playback_is_playing'])
        self.assertEqual(queue['playback_progress_ms'], 1200)
        self.dj.spotify.start_playback.assert_called_once()

    async def test_repeated_manual_failure_reports_failure_and_pauses_again(self):
        self.dj.spotify.current_playback.return_value = playback(None, False)
        await self.service._queue_start_next_if_needed()
        self.assertFalse(await self.service.resume_queue())
        self.assertTrue(self.service._queue_paused)
        self.assertEqual(len(self.service._queue_items), 1)

    async def test_resume_api_returns_the_playback_failure_message(self):
        self.dj.spotify.current_playback.return_value = playback(None, False)
        web = application.WebUI.__new__(application.WebUI)
        web._service = self.service

        response = await web._api_resume(None)
        payload = json.loads(response.text)
        self.assertFalse(payload['ok'])
        self.assertIn('did not start playback', payload['error'])
        self.assertIn('still queued', payload['error'])

    async def test_resume_api_explains_an_attempt_already_in_progress(self):
        self.service._queue_starting = True
        web = application.WebUI.__new__(application.WebUI)
        web._service = self.service

        response = await web._api_resume(None)
        payload = json.loads(response.text)
        self.assertFalse(payload['ok'])
        self.assertIn('still starting', payload['error'])
        self.dj.spotify.start_playback.assert_not_called()

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

    async def _confirm_first_track(self, progress_ms, *, next_uri=NEXT_URI):
        if next_uri:
            self.service._queue_items.append({'source': 'spotify', 'uri': next_uri, 'queue_entry_id': 'second'})
        self.dj.spotify.current_playback.return_value = playback(progress_ms=progress_ms, duration_ms=240000, timestamp=1000)
        self.assertTrue(await self.service._queue_start_next_if_needed())
        self.service._queue_started_ts -= 6
        observation = self.service._queue_spotify_observation
        self.service._queue_spotify_observation = replace(observation, observed_at=observation.observed_at - 6)

    async def _watchdog_polls(self, states, *, polls=None):
        self.dj.ensure_playback_device_active = Mock()
        self.dj.check_queue_status = Mock()
        self.dj.spotify.current_playback.side_effect = states
        self.service._stop_event = Mock(is_set=Mock(side_effect=[False] * (polls or len(states)) + [True]), wait=AsyncMock())
        await self.service._queue_watchdog()

    async def test_natural_end_with_spotify_autoplay_starts_the_next_request_directly(self):
        await self._confirm_first_track(235000)
        await self._watchdog_polls([
            playback(OTHER_URI, progress_ms=200, timestamp=2000),
            playback(OTHER_URI, progress_ms=5200, timestamp=2000),
            playback(NEXT_URI),
        ], polls=2)
        self.assertEqual(self.service._queue_now_playing['queue_entry_id'], 'second')
        self.assertFalse(self.service._queue_paused)
        self.dj.spotify.start_playback.assert_called_with(device_id='desktop', uris=[NEXT_URI])
        self.dj.spotify.next_track.assert_not_called()

    async def test_explicit_skip_sends_spotify_next(self):
        await self._confirm_first_track(10000)
        self.dj.spotify.current_playback.return_value = playback(NEXT_URI)
        web = application.WebUI.__new__(application.WebUI)
        web._service = self.service
        self.assertTrue(json.loads((await web._api_queue_next(None)).text)['ok'])
        self.dj.spotify.next_track.assert_called_once_with(device_id='desktop')
        self.assertEqual(self.service._queue_now_playing['queue_entry_id'], 'second')

    async def test_early_replacement_holds_requests_until_resume(self):
        await self._confirm_first_track(10000)
        pending = self.service._queue_items[0]
        await self._watchdog_polls([
            playback(OTHER_URI, progress_ms=100, timestamp=2000),
            playback(OTHER_URI, progress_ms=5100, timestamp=2000),
        ])
        self.assertTrue(self.service._queue_paused)
        self.assertIsNone(self.service._queue_now_playing)
        self.assertIs(self.service._queue_items[0], pending)
        self.service._enrich_mixed_queue_items = AsyncMock(side_effect=lambda items: items)
        self.assertIn('Spotify playback changed. Select Resume Queue', (await self.service.get_queue_state())['playback_error'])
        self.dj.spotify.start_playback.assert_called_once()
        self.dj.spotify.next_track.assert_not_called()

        self.dj.spotify.current_playback.side_effect = None
        self.dj.spotify.current_playback.return_value = playback(NEXT_URI)
        self.assertTrue(await self.service.resume_queue())
        self.assertIsNone(self.service._queue_error)
        self.assertEqual(self.service._queue_now_playing['queue_entry_id'], 'second')
        self.dj.spotify.next_track.assert_not_called()

    async def test_early_replacement_with_empty_queue_does_not_skip(self):
        await self._confirm_first_track(10000, next_uri=None)
        await self._watchdog_polls([
            playback(OTHER_URI, progress_ms=300, timestamp=2000),
            playback(OTHER_URI, progress_ms=5300, timestamp=2000),
        ])
        self.assertIsNone(self.service._queue_now_playing)
        self.assertTrue(self.service._queue_paused)
        self.dj.spotify.next_track.assert_not_called()

    async def test_paused_scheduling_tracks_end_but_waits_for_resume(self):
        await self._confirm_first_track(237000)
        await self.service.pause_queue()
        stopped = playback(playing=False, progress_ms=0, duration_ms=240000, timestamp=2000)
        await self._watchdog_polls([stopped, stopped])
        self.assertIsNone(self.service._queue_now_playing)
        self.assertTrue(self.service._queue_paused)
        self.dj.spotify.start_playback.assert_called_once()
        self.dj.spotify.next_track.assert_not_called()

        self.dj.spotify.current_playback.side_effect = None
        self.dj.spotify.current_playback.return_value = playback(NEXT_URI)
        self.assertTrue(await self.service.resume_queue())
        self.assertEqual(self.service._queue_now_playing['queue_entry_id'], 'second')

    async def test_playing_at_end_and_media_pause_do_not_retire_request(self):
        await self._confirm_first_track(239000)
        first = self.service._queue_now_playing
        near_end = playback(progress_ms=239900, duration_ms=240000, timestamp=1000)
        await self._watchdog_polls([near_end, near_end])
        self.assertIs(self.service._queue_now_playing, first)
        self.assertTrue(await self.service.pause_playback())
        self.dj.spotify.current_playback.reset_mock()
        await self._watchdog_polls([None, None])
        self.assertIs(self.service._queue_now_playing, first)
        self.dj.spotify.current_playback.assert_not_called()
        self.dj.spotify.start_playback.assert_called_once()

    async def test_spotify_pause_near_end_does_not_retire_the_request(self):
        await self._confirm_first_track(238000)
        first = self.service._queue_now_playing
        paused = playback(playing=False, progress_ms=239000, duration_ms=240000, timestamp=2000)
        await self._watchdog_polls([paused, paused])
        self.assertIs(self.service._queue_now_playing, first)
        self.dj.spotify.start_playback.assert_called_once()

    async def test_missing_failed_wrong_device_and_stale_reads_do_not_establish_a_switch(self):
        await self._confirm_first_track(10000)
        first = self.service._queue_now_playing
        frozen = playback(OTHER_URI, progress_ms=300, timestamp=2000)
        await self._watchdog_polls([
            frozen, frozen, TimeoutError(), playback(OTHER_URI, progress_ms=5300, timestamp=2000),
            playback(OTHER_URI, progress_ms=6000, timestamp=900), None,
            playback(OTHER_URI, device='other-device', progress_ms=10000, timestamp=2000),
        ])
        self.assertIs(self.service._queue_now_playing, first)
        self.assertFalse(self.service._queue_paused)
        self.dj.spotify.next_track.assert_not_called()

    async def test_watchdog_discards_an_observation_if_the_same_song_has_a_new_entry(self):
        await self._confirm_first_track(10000, next_uri=URI)
        first = self.service._queue_now_playing
        await self._watchdog_polls([playback(OTHER_URI, progress_ms=300, timestamp=2000)])
        entered, release = threading.Event(), threading.Event()

        def read():
            entered.set()
            release.wait(timeout=3)
            return playback(OTHER_URI, progress_ms=5300, timestamp=2000)

        self.dj.spotify.current_playback.side_effect = read
        check = asyncio.create_task(self.service._reconcile_spotify_queue_item(first))
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait, 2))
            second = self.service._queue_items.pop(0)
            self.service._queue_now_playing = second
        finally:
            release.set()
        await check
        self.assertIs(self.service._queue_now_playing, second)
        self.assertFalse(self.service._queue_paused)
        self.dj.spotify.next_track.assert_not_called()

    async def test_duplicate_at_old_position_stays_queued_and_a_late_reset_is_adopted(self):
        await self._confirm_first_track(239000, next_uri=URI)
        stopped = playback(playing=False, progress_ms=240000, duration_ms=240000, timestamp=2000)
        stale = playback(progress_ms=190000, duration_ms=240000, timestamp=1000)
        await self._watchdog_polls([stopped, stopped] + [stale] * 12, polls=2)
        self.assertTrue(self.service._queue_paused)
        self.assertIn('confirm that the requested song restarted', self.service._queue_error)
        self.assertEqual(self.service._queue_items[0]['queue_entry_id'], 'second')
        self.dj.spotify.start_playback.assert_called_with(device_id='desktop', uris=[URI], position_ms=0)

        attempt = self.service._queue_spotify_attempt[2]
        self.dj.spotify.current_playback.side_effect = None
        self.dj.spotify.current_playback.return_value = playback(progress_ms=100, timestamp=attempt.sent_ms)
        self.assertTrue(await self.service.resume_queue())
        self.assertEqual(self.dj.spotify.start_playback.call_count, 2)
        self.assertEqual(self.service._queue_now_playing['queue_entry_id'], 'second')
        self.assertEqual(self.service._queue_items, [])
        self.dj.spotify.next_track.assert_not_called()

    async def test_duplicate_confirms_fresh_start_after_finished_item_resets_to_zero(self):
        await self._confirm_first_track(239000, next_uri=URI)
        stopped = playback(playing=False, progress_ms=0, duration_ms=240000, timestamp=2000)
        reads = iter([stopped, stopped, stopped, playback(progress_ms=100, timestamp=2000)])

        def read():
            return next(reads, None) or playback(progress_ms=100, timestamp=int(time.time() * 1000))

        self.dj.ensure_playback_device_active = Mock()
        self.dj.check_queue_status = Mock()
        self.dj.spotify.current_playback.side_effect = read
        self.service._stop_event = Mock(is_set=Mock(side_effect=[False, False, True]), wait=AsyncMock())
        await self.service._queue_watchdog()
        self.assertEqual(self.service._queue_now_playing['queue_entry_id'], 'second')
        self.dj.spotify.start_playback.assert_called_with(device_id='desktop', uris=[URI], position_ms=0)
        self.assertEqual(self.dj.spotify.current_playback.call_count, 6)
        self.dj.spotify.next_track.assert_not_called()

    async def test_resume_adopts_duplicate_after_stopped_at_zero_then_fresh_start(self):
        await self._confirm_first_track(239000, next_uri=URI)
        stopped = playback(playing=False, progress_ms=0, duration_ms=240000, timestamp=2000)
        await self._watchdog_polls([stopped] * 14, polls=2)
        self.assertEqual(self.service._queue_items[0]['queue_entry_id'], 'second')
        attempt = self.service._queue_spotify_attempt[2]
        self.dj.spotify.current_playback.side_effect = None
        self.dj.spotify.current_playback.return_value = playback(progress_ms=100, timestamp=attempt.sent_ms)
        self.assertTrue(await self.service.resume_queue())
        self.assertEqual(self.service._queue_now_playing['queue_entry_id'], 'second')
        self.assertEqual(self.dj.spotify.start_playback.call_count, 2)

    async def test_reorder_during_late_check_invalidates_the_prior_attempt(self):
        self.service._queue_items.append({'source': 'spotify', 'uri': NEXT_URI, 'queue_entry_id': 'second'})
        self.dj.spotify.current_playback.return_value = playback(None, False)
        self.assertFalse(await self.service._queue_start_next_if_needed())
        prior = self.service._queue_spotify_attempt[2]
        first = self.service._queue_items[0]
        entered, release = threading.Event(), threading.Event()

        def read():
            entered.set()
            release.wait(timeout=3)
            return playback(progress_ms=100, timestamp=prior.sent_ms)

        self.dj.spotify.current_playback.side_effect = read
        resume = asyncio.create_task(self.service.resume_queue())
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait, 2))
            self.assertFalse(await self.service._queue_start_next_if_needed())
            await self.service.move_queue_item(0, 1)
            await self.service.move_queue_item(1, 0)
        finally:
            release.set()
        self.assertFalse(await resume)
        self.assertIs(self.service._queue_items[0], first)
        self.assertTrue(self.service._queue_paused)
        self.assertEqual(self.dj.spotify.start_playback.call_count, 2)

    async def test_late_play_on_a_previous_device_cannot_confirm_the_current_head(self):
        self.dj.spotify.current_playback.return_value = playback(None, False)
        self.assertFalse(await self.service._queue_start_next_if_needed())
        prior = self.service._queue_spotify_attempt[2]
        self.dj.spotify.current_playback.return_value = playback(progress_ms=100, timestamp=prior.sent_ms)
        self.dj.spotify.devices.return_value = {'devices': [{'id': 'new-device', 'name': 'Desktop'}]}
        self.assertTrue(await self.service.set_device('new-device', persist=False))
        self.assertFalse(await self.service.resume_queue())
        self.assertEqual(self.dj.spotify.start_playback.call_count, 2)
        self.dj.spotify.start_playback.assert_called_with(device_id='new-device', uris=[URI], position_ms=0)
        self.assertEqual(len(self.service._queue_items), 1)

    async def test_removing_a_failed_request_invalidates_late_proof_for_an_identical_request(self):
        self.dj.spotify.current_playback.return_value = playback(None, False)
        self.assertFalse(await self.service._queue_start_next_if_needed())
        prior = self.service._queue_spotify_attempt[2]
        await self.service.delete_queue_item(0)
        await self.service.add_track_to_queue(URI)
        replacement = self.service._queue_items[0]
        self.dj.spotify.current_playback.return_value = playback(progress_ms=100, timestamp=prior.sent_ms)

        self.assertFalse(await self.service.resume_queue())
        self.assertIs(self.service._queue_items[0], replacement)
        self.assertTrue(self.service._queue_paused)
        self.assertEqual(self.dj.spotify.start_playback.call_count, 2)

    async def test_youtube_completion_applies_to_exact_entry_and_never_skips_spotify(self):
        web = application.WebUI.__new__(application.WebUI)
        web._service = self.service
        self.dj.spotify.current_playback.return_value = playback()
        self.service._queue_now_playing = {'source': 'youtube', 'queue_entry_id': 'youtube'}
        request = SimpleNamespace(json=AsyncMock(return_value={'queue_entry_id': 'youtube'}))
        self.assertTrue(json.loads((await web._api_queue_complete(request)).text)['completed'])
        self.assertEqual(self.service._queue_now_playing['queue_entry_id'], 'first')
        self.assertFalse(json.loads((await web._api_queue_complete(request)).text)['completed'])
        self.dj.spotify.next_track.assert_not_called()


if __name__ == '__main__':
    unittest.main()
