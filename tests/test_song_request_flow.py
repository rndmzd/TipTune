import configparser
import asyncio
import importlib
import json
import logging
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import ANY, AsyncMock, Mock, patch

from chatdj.song_requests import SongRequest, SongExtractionError, SongResolutionError
from utils.error_reporting import ErrorReporter


class SongRequestFlowTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.runtime = tempfile.TemporaryDirectory()
        root = Path(cls.runtime.name)
        cls.env = patch.dict(os.environ, {'TIPTUNE_CONFIG': str(root/'config.ini'),
            'TIPTUNE_CACHE_DIR': str(root/'cache'), 'TIPTUNE_DEFAULT_LOG_PATH': str(root/'test.log')})
        cls.env.start()
        cls.app = importlib.import_module('app')
        from helpers.actions import Actions
        cls.Actions = Actions

    @classmethod
    def tearDownClass(cls):
        cls.env.stop()
        logging.shutdown()
        cls.runtime.cleanup()

    def setUp(self):
        checks_config = configparser.ConfigParser()
        checks_config.read_dict({'General': {'song_cost': '27', 'skip_song_cost': '51'}})
        with patch('helpers.config', checks_config):
            checks = self.app.Checks()
        self.actions = SimpleNamespace(
            extract_song_titles=AsyncMock(return_value=[SongRequest(song='Hello', artist='Adele')]),
            find_song_spotify=AsyncMock(return_value='spotify:track:'+'A'*22),
            available_in_market=AsyncMock(return_value=True),
            trigger_warning_overlay=AsyncMock(), trigger_song_requester_overlay=AsyncMock())
        self.service = SimpleNamespace(
            checks=checks,
            actions=self.actions, _active_source=Mock(return_value='spotify'),
            _allow_source_override_in_request_message=Mock(return_value=False),
            _youtube_url_from_text=Mock(return_value=None),
            search_youtube_tracks=AsyncMock(return_value=[{'uri': 'https://youtu.be/abc'}]),
            publish_request_history_item=Mock(), add_track_to_queue=AsyncMock(return_value=True),
            advance_queue=AsyncMock(return_value=True),
            _get_obs_overlay_duration_seconds=Mock(return_value=10))

    async def handle(self, message='Hello by Adele', tokens=27):
        await self.app.SongRequestService._handle_tip(self.service,
            {'tip': {'tokens': tokens, 'message': message}, 'user': {'username': 'Test'}})

    def history(self):
        return [call.args[0] for call in self.service.publish_request_history_item.call_args_list]

    def enable_reports(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        config_path = root / 'reporting.ini'
        config_path.write_text('[General]\nsend_error_reports=true')
        reporter = ErrorReporter(config_path, root / 'cache', 'https://public@example.org/1', start_worker=False)
        self.addCleanup(reporter.close)
        self.enterContext(patch.object(self.app, 'error_reporter', reporter))
        return reporter

    async def test_lookup_exception_creates_a_report_with_the_history_support_id(self):
        reporter = self.enable_reports()
        self.actions.find_song_spotify.side_effect = TimeoutError('private-provider-response')
        await self.handle()
        item = self.history()[0]
        events = [json.loads(path.read_text())['event'] for path in reporter.pending_dir.glob('*.json')]
        error = next(event for event in events if event.get('type') != 'transaction')
        self.assertEqual(error['tags']['error_code'], 'song.lookup.failed')
        self.assertEqual(error['tags']['support_id'], item['support']['support_id'])
        self.assertEqual(error['event_id'], item['support']['report_id'])
        self.assertEqual(error['contexts']['failure']['source'], 'spotify')
        self.assertNotIn('private-provider-response', json.dumps(error))

    async def test_youtube_lookup_exception_and_queue_rejection_have_distinct_codes(self):
        reporter = self.enable_reports()
        self.service._active_source.return_value = 'youtube'
        self.service.search_youtube_tracks.side_effect = TimeoutError('private')
        await self.handle()
        self.assertEqual(self.history()[0]['support']['error_code'], 'song.lookup.failed')
        self.service._active_source.return_value = 'spotify'
        self.service.add_track_to_queue.return_value = False
        await self.handle()
        self.assertEqual(self.history()[1]['support']['error_code'], 'song.queue.failed')
        self.assertNotEqual(self.history()[0]['support']['support_id'], self.history()[1]['support']['support_id'])
        events = [json.loads(path.read_text())['event'] for path in reporter.pending_dir.glob('*.json')]
        self.assertEqual(len([event for event in events if event.get('type') != 'transaction']), 2)

    async def test_normal_catalog_miss_keeps_a_local_id_without_an_error_report(self):
        reporter = self.enable_reports()
        self.actions.find_song_spotify.return_value = None
        await self.handle()
        self.assertTrue(self.history()[0]['support']['support_id'].startswith('TT-'))
        events = [json.loads(path.read_text())['event'] for path in reporter.pending_dir.glob('*.json')]
        self.assertFalse(any(event.get('type') != 'transaction' for event in events))

    async def test_only_exact_song_cost_triggers_a_request(self):
        for tokens in (0, -27, 26, 28, 54, 81, 100, 27.0, '27', True):
            with self.subTest(tokens=tokens):
                await self.handle(tokens=tokens)
        self.actions.extract_song_titles.assert_not_called()
        self.service.add_track_to_queue.assert_not_called()
        self.assertEqual(self.history(), [])
        await self.handle()
        self.service.add_track_to_queue.assert_awaited_once()
        self.assertEqual(self.history()[0]['request_count'], 1)

    async def test_legacy_setting_cannot_enable_request_multiples(self):
        for setting in ('true', 'false'):
            with self.subTest(setting=setting):
                legacy_config = configparser.ConfigParser()
                legacy_config.read_dict({'General': {'song_cost': '13', 'multi_request_tips': setting}})
                with patch('helpers.config', legacy_config):
                    self.service.checks = self.app.Checks()
                self.assertTrue(self.service.checks.is_song_request(13))
                self.assertFalse(self.service.checks.is_song_request(13.0))
                self.assertFalse(self.service.checks.is_song_request(True))
                await self.handle(tokens=26)
        self.actions.extract_song_titles.assert_not_called()
        self.service.add_track_to_queue.assert_not_called()

    async def test_skip_cost_multiples_still_skip(self):
        for tokens in (51, 102):
            with self.subTest(tokens=tokens):
                await self.handle(tokens=tokens)
        self.assertEqual(self.service.advance_queue.await_count, 2)
        self.actions.extract_song_titles.assert_not_called()

    async def test_exact_song_request_takes_precedence_over_skip(self):
        self.service.checks.song_cost = 51
        await self.handle(tokens=51)
        self.service.add_track_to_queue.assert_awaited_once()
        self.service.advance_queue.assert_not_called()

    async def test_no_song_does_not_search_original_message(self):
        self.actions.extract_song_titles.return_value = []
        await self.handle('thank you')
        self.assertEqual(self.history()[0]['error'], 'no song identified')
        self.actions.find_song_spotify.assert_not_called()
        self.service.add_track_to_queue.assert_not_called()

    async def test_extraction_failure_is_separate_from_no_song(self):
        self.actions.extract_song_titles.side_effect = SongExtractionError('timeout')
        await self.handle()
        self.assertEqual(self.history()[0]['error'], 'song extraction failed')
        debug = self.history()[0]['extraction_debug']
        self.assertEqual(debug['exception']['message'], 'timeout')
        self.assertEqual(debug['request_count'], 1)
        self.assertEqual(debug['music_source'], 'spotify')
        self.assertEqual(debug['outcome'], 'extraction_failed')
        self.assertGreaterEqual(debug['elapsed_ms'], 0)
        self.actions.find_song_spotify.assert_not_called()

    async def test_tip_worker_links_extraction_failure_to_the_request_trace(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            config_path = root / 'config.ini'
            config_path.write_text('[General]\nsend_error_reports=true')
            reporter = ErrorReporter(config_path, root / 'cache', 'https://public@example.org/1',
                                     start_worker=False, traces_sample_rate=1.0)
            self.addCleanup(reporter.close)
            self.actions.extract_song_titles.side_effect = SongExtractionError('private provider response')
            worker = SimpleNamespace(_stop_event=asyncio.Event(), _tip_queue=asyncio.Queue())

            async def handle_tip(event):
                await self.app.SongRequestService._handle_tip(self.service, event)
                worker._stop_event.set()

            worker._handle_tip = handle_tip
            await worker._tip_queue.put({'tip': {'tokens': 27, 'message': 'private request'},
                                         'user': {'username': 'private username'}})
            with patch.object(self.app, 'error_reporter', reporter), patch('utils.error_reporting.httpx.post') as post:
                await self.app.SongRequestService._tip_processor_loop(worker)
                post.assert_not_called()
            events = [json.loads(path.read_text())['event'] for path in reporter.pending_dir.glob('*.json')]
            self.assertEqual(len(events), 2)
            error = next(event for event in events if event.get('type') != 'transaction')
            transaction = next(event for event in events if event.get('type') == 'transaction')
            self.assertEqual(transaction['transaction'], 'song.request')
            self.assertEqual(transaction['spans'][0]['description'], 'song.extract')
            self.assertEqual(error['contexts']['trace']['span_id'], transaction['spans'][0]['span_id'])
            self.assertEqual(error['contexts']['trace']['trace_id'], transaction['contexts']['trace']['trace_id'])
            self.assertNotIn('private', json.dumps(events))

    async def test_failure_retains_model_response_and_exception_cause(self):
        error = SongExtractionError('Song extraction failed', diagnostics={
            'stage': 'response_validation', 'model': 'test-model',
            'response': {'id': 'resp_test', 'status': 'incomplete',
                         'incomplete_details': {'reason': 'max_output_tokens'}},
        })
        error.__cause__ = ValueError('Incomplete model response')
        self.actions.extract_song_titles.side_effect = error
        await self.handle()
        debug = self.history()[0]['extraction_debug']
        self.assertEqual(debug['stage'], 'response_validation')
        self.assertEqual(debug['model'], 'test-model')
        self.assertEqual(debug['response']['id'], 'resp_test')
        self.assertEqual(debug['exception']['chain'][-1]['message'], 'Incomplete model response')
        self.assertIn('ValueError', debug['exception']['traceback'])

    async def test_history_redacts_configured_credentials_from_errors(self):
        self.app.config.read_string('[OpenAI]\napi_key = private-test-credential\n[OBS]\npassword = private%%password')
        self.addCleanup(self.app.config.remove_section, 'OpenAI')
        self.addCleanup(self.app.config.remove_section, 'OBS')
        self.actions.extract_song_titles.side_effect = RuntimeError(
            'Bad credential: private-test-credential; OBS private%password')
        await self.handle()
        import json
        saved = json.dumps(self.history()[0]['extraction_debug'])
        self.assertNotIn('private-test-credential', saved)
        self.assertNotIn('private%password', saved)
        self.assertIn('[REDACTED]', saved)

    async def test_empty_output_retains_per_call_model_diagnostics(self):
        async def extract(message, *, diagnostics):
            diagnostics.update({'stage': 'grounding_validation', 'response': {'output_parsed': {'songs': []}}})
            return []
        self.actions.extract_song_titles.side_effect = extract
        await self.handle('thank you')
        debug = self.history()[0]['extraction_debug']
        self.assertEqual(debug['response']['output_parsed'], {'songs': []})
        self.assertEqual(debug['outcome'], 'no_songs_identified')

    async def test_blank_input_has_diagnostics_without_calling_model(self):
        await self.handle('   ')
        debug = self.history()[0]['extraction_debug']
        self.assertEqual(debug['stage'], 'input_validation')
        self.assertEqual(debug['outcome'], 'blank_tip_message')
        self.actions.extract_song_titles.assert_not_called()

    async def test_unconfigured_extractor_reports_configuration_failure(self):
        actions = SimpleNamespace(chatdj_enabled=False)
        self.actions.extract_song_titles = lambda *args, **kwargs: self.Actions.extract_song_titles(actions, *args, **kwargs)
        await self.handle()
        self.assertEqual(self.history()[0]['extraction_debug']['stage'], 'configuration')
        self.assertIn('not configured', self.history()[0]['extraction_debug']['exception']['message'])

    async def test_short_title_is_unmodified(self):
        await self.handle('U')
        self.actions.extract_song_titles.assert_awaited_once_with('U', diagnostics=ANY)

    async def test_only_first_song_is_queued_even_if_extractor_returns_more(self):
        self.actions.extract_song_titles.return_value = [SongRequest(song='One', artist=''),
                                                        SongRequest(song='Two', artist='')]
        await self.handle('One; Two')
        self.actions.find_song_spotify.assert_awaited_once_with(self.actions.extract_song_titles.return_value[0])
        self.service.add_track_to_queue.assert_awaited_once()
        self.assertEqual(len(self.history()), 1)
        self.assertEqual(self.history()[0]['song'], 'One')
        self.assertEqual(self.history()[0]['request_count'], 1)

    async def test_failed_first_song_does_not_process_another_request(self):
        self.actions.extract_song_titles.return_value = [SongRequest(song='One', artist=''),
                                                        SongRequest(song='Two', artist='')]
        self.actions.find_song_spotify.side_effect = [SongResolutionError('failed'), 'spotify:track:'+'B'*22]
        await self.handle('One; Two')
        self.assertEqual([h['status'] for h in self.history()], ['failed'])
        self.assertEqual(self.history()[0]['error'], 'spotify lookup failed')
        self.service.add_track_to_queue.assert_not_called()
        self.actions.find_song_spotify.assert_awaited_once()

    async def test_queue_failure_is_not_reported_as_added(self):
        for failure in [False, RuntimeError('queue failed')]:
            with self.subTest(failure=type(failure).__name__):
                self.service.publish_request_history_item.reset_mock()
                self.service.add_track_to_queue.side_effect = failure if isinstance(failure, Exception) else None
                self.service.add_track_to_queue.return_value = failure
                await self.handle()
                self.assertEqual(self.history()[0]['status'], 'failed')
                self.assertEqual(self.history()[0]['error'], 'queue insertion failed')
        self.actions.trigger_song_requester_overlay.assert_not_called()

    async def test_direct_spotify_uri_is_authoritative(self):
        self.actions.extract_song_titles.return_value = [SongRequest(song='Hello', artist='Adele',
                                                                    spotify_uri='spotify:track:'+'B'*22)]
        await self.handle()
        self.actions.find_song_spotify.assert_not_called()
        self.assertEqual(self.history()[0]['resolved_uri'], 'spotify:track:'+'B'*22)

    async def test_unavailable_track_is_not_queued(self):
        self.actions.available_in_market.return_value = False
        await self.handle()
        self.assertEqual(self.history()[0]['error'], 'not available in market')
        self.service.add_track_to_queue.assert_not_called()

    async def test_youtube_search_uses_shared_extraction(self):
        self.service._active_source.return_value = 'youtube'
        await self.handle()
        self.actions.extract_song_titles.assert_awaited_once()
        self.service.search_youtube_tracks.assert_awaited_once_with('Adele - Hello', limit=1)
        self.assertEqual(self.history()[0]['resolved_uri'], 'https://youtu.be/abc')
        self.actions.find_song_spotify.assert_not_called()

    async def test_direct_youtube_bypasses_model_failure(self):
        self.service._active_source.return_value = 'youtube'
        self.service._youtube_url_from_text.return_value = 'https://youtu.be/abc'
        self.actions.extract_song_titles.side_effect = SongExtractionError('unavailable')
        await self.handle('https://youtu.be/abc')
        self.actions.extract_song_titles.assert_not_called()
        self.assertEqual(self.history()[0]['status'], 'added')

    async def test_actions_populates_catalog_metadata(self):
        song = SongRequest(song='Helo', artist='')
        resolved = SongRequest(song='Hello', artist='Adele', spotify_uri='spotify:track:'+'A'*22)
        actions = SimpleNamespace(chatdj_enabled=True, song_extractor=Mock())
        actions.song_extractor.resolve_spotify.return_value = resolved
        uri = await self.Actions.find_song_spotify(actions, song)
        self.assertEqual(song.model_dump(), resolved.model_dump())
        self.assertEqual(uri, resolved.spotify_uri)
        actions.song_extractor.reset_mock()
        await self.Actions.find_song_spotify(actions, song)
        actions.song_extractor.resolve_spotify.assert_not_called()

    async def test_availability_checks_market_playability_and_failures(self):
        spotify = Mock()
        spotify.me.return_value = {'country': 'CA'}
        actions = SimpleNamespace(chatdj_enabled=True, auto_dj=SimpleNamespace(spotify=spotify))
        for info, expected in [({'uri': 'track', 'is_playable': True}, True),
                               ({'uri': 'track', 'is_playable': False}, False),
                               ({'uri': 'track', 'available_markets': []}, False),
                               ({'uri': 'track', 'available_markets': [], 'is_playable': True}, True),
                               ({'uri': 'track', 'available_markets': ['US']}, False)]:
            with self.subTest(info=info):
                spotify.track.return_value = info
                self.assertEqual(await self.Actions.available_in_market(actions, 'track'), expected)
                spotify.track.assert_called_with('track', market='CA')
        spotify.track.side_effect = RuntimeError('lookup failed')
        self.assertFalse(await self.Actions.available_in_market(actions, 'track'))


if __name__ == '__main__':
    unittest.main()
