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
            spotify_unavailability=AsyncMock(return_value=None),
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

    def notice(self):
        username, message, duration = self.actions.trigger_warning_overlay.call_args.args
        self.assertEqual(username, 'Test')
        self.assertGreaterEqual(duration, 15)
        return message

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

    async def test_spotify_restriction_is_not_reported_as_a_service_failure(self):
        reporter = self.enable_reports()
        self.actions.spotify_unavailability.return_value = 'market'
        await self.handle()
        self.assertEqual(self.history()[0]['error'], 'not available in market')
        self.assertTrue(self.history()[0]['support']['support_id'].startswith('TT-'))
        self.assertNotIn('error_code', self.history()[0]['support'])
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
        message = self.notice()
        self.assertIn('nothing was queued', message)
        self.assertIn('song title and artist', message)
        self.assertIn('Spotify link in chat', message)
        self.assertIn('before tipping again', message)

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
        self.assertIn("TipTune couldn't read", self.notice())
        self.assertIn('before tipping again', self.notice())

    async def test_missing_message_explains_both_causes_and_how_to_recover(self):
        for tip in ({'tokens': 27}, {'tokens': 27, 'message': None},
                    {'tokens': 27, 'message': ''}, {'tokens': 27, 'message': ' \n\t '}):
            with self.subTest(tip=tip):
                self.actions.trigger_warning_overlay.reset_mock()
                await self.app.SongRequestService._handle_tip(self.service,
                    {'tip': tip, 'user': {'username': 'Test'}})
                message = self.notice()
                self.assertIn('arrived without a message', message)
                self.assertIn('may have been left blank', message)
                self.assertIn("site's word filter", message)
                self.assertIn('song title and artist in chat', message)
                self.assertIn('before tipping again', message)
                self.assertEqual(self.history()[-1]['error'], 'blank tip message')
                self.actions.trigger_warning_overlay.assert_awaited_once()
        self.actions.extract_song_titles.assert_not_called()
        self.service.add_track_to_queue.assert_not_called()

    async def test_ordinary_tips_do_not_get_song_request_notices(self):
        await self.handle(None, tokens=26)
        self.actions.trigger_warning_overlay.assert_not_called()
        self.assertEqual(self.history(), [])

    async def test_notices_honor_a_longer_configured_duration(self):
        self.service._get_obs_overlay_duration_seconds.return_value = 25
        await self.handle('')
        self.assertEqual(self.actions.trigger_warning_overlay.call_args.args[2], 25)

    async def test_lookup_outage_and_catalog_miss_offer_different_help(self):
        for source in ('spotify', 'youtube'):
            for outage in (False, True):
                with self.subTest(source=source, outage=outage):
                    self.service._active_source.return_value = source
                    lookup = self.service.search_youtube_tracks if source == 'youtube' else self.actions.find_song_spotify
                    lookup.return_value = [] if source == 'youtube' else None
                    lookup.side_effect = TimeoutError('private-provider-response') if outage else None
                    await self.handle()
                    message = self.notice()
                    self.assertIn('YouTube' if source == 'youtube' else 'Spotify', message)
                    self.assertIn('before tipping again', message)
                    self.assertNotIn('private-provider-response', message)
                    if outage:
                        self.assertIn("Couldn't search", message)
                        self.assertIn('not queued', message)
                    else:
                        self.assertIn('link in chat', message)
                        if source == 'spotify':
                            self.assertIn("streamer's Spotify catalog", message)
                            self.assertIn('country', message)
        self.service.add_track_to_queue.assert_not_called()

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
                self.assertIn("couldn't add it to the queue", self.notice())
                self.assertIn('before tipping again', self.notice())
        self.actions.trigger_song_requester_overlay.assert_not_called()

    async def test_direct_spotify_uri_is_authoritative(self):
        self.actions.extract_song_titles.return_value = [SongRequest(song='Hello', artist='Adele',
                                                                    spotify_uri='spotify:track:'+'B'*22)]
        await self.handle()
        self.actions.find_song_spotify.assert_not_called()
        self.assertEqual(self.history()[0]['resolved_uri'], 'spotify:track:'+'B'*22)

    async def test_unavailable_track_is_not_queued(self):
        self.actions.spotify_unavailability.return_value = 'market'
        await self.handle()
        self.assertEqual(self.history()[0]['error'], 'not available in market')
        self.assertIn("streamer's Spotify country", self.notice())
        self.assertIn('another version or a different song', self.notice())
        self.assertIn('before tipping again', self.notice())
        self.service.add_track_to_queue.assert_not_called()

    async def test_nonregional_spotify_restrictions_get_specific_guidance(self):
        for reason, advice in [('explicit', 'clean version'), ('product', 'subscription'),
                               ('local', 'local files'), ('unavailable', 'another version'),
                               ('future_reason', 'another version')]:
            with self.subTest(reason=reason):
                self.actions.spotify_unavailability.return_value = reason
                await self.handle()
                self.assertIn(advice, self.notice())
                self.assertNotIn('country', self.notice())
                self.assertIn('before tipping again', self.notice())
                self.assertEqual(self.history()[-1]['status'], 'failed')
        self.service.add_track_to_queue.assert_not_called()

    async def test_availability_outage_is_not_a_country_restriction(self):
        reporter = self.enable_reports()
        self.actions.spotify_unavailability.side_effect = SongResolutionError('private-provider-response')
        await self.handle()
        item = self.history()[0]
        self.assertEqual(item['error'], 'spotify availability check failed')
        self.assertEqual(item['support']['error_code'], 'song.availability.failed')
        message = self.notice()
        self.assertIn("Couldn't check", message)
        self.assertIn("wasn't queued", message)
        self.assertNotIn('country', message)
        self.assertNotIn('private-provider-response', message)
        self.service.add_track_to_queue.assert_not_called()
        events = [json.loads(path.read_text())['event'] for path in reporter.pending_dir.glob('*.json')]
        error = next(event for event in events if event.get('type') != 'transaction')
        self.assertEqual(error['tags']['support_id'], item['support']['support_id'])

    async def test_queued_requests_explain_paused_or_failed_playback(self):
        for flag, error in [('_queue_paused', None), ('_queue_playback_paused', None),
                            ('_queue_paused', 'private device error')]:
            with self.subTest(flag=flag, error=error):
                self.service._queue_paused = self.service._queue_playback_paused = False
                setattr(self.service, flag, True)
                self.service._queue_error = error
                await self.handle()
                self.assertEqual(self.history()[-1]['status'], 'added')
                self.assertIn('saved in the queue', self.notice())
                self.assertIn('no need to tip again', self.notice())
                self.assertIn("streamer's attention" if error else 'paused', self.notice())
                self.assertNotIn('private device error', self.notice())
        self.assertEqual(self.actions.trigger_song_requester_overlay.await_count, 3)

    async def test_success_does_not_warn_and_short_title_has_no_dangling_separator(self):
        self.actions.extract_song_titles.return_value = [SongRequest(song='U', artist='')]
        await self.handle('U')
        self.actions.trigger_song_requester_overlay.assert_awaited_once_with('Test', 'U', 10)
        self.actions.trigger_warning_overlay.assert_not_called()
        self.assertEqual(self.history()[0]['status'], 'added')

    async def test_notice_display_failure_does_not_change_or_lose_the_queue_result(self):
        self.actions.trigger_warning_overlay.side_effect = RuntimeError('OBS disconnected')
        self.service._queue_paused = True
        for accepted in (True, False):
            with self.subTest(accepted=accepted):
                self.service.add_track_to_queue.return_value = accepted
                await self.handle()
                self.assertEqual(self.history()[-1]['status'], 'added' if accepted else 'failed')
        self.assertEqual(len(self.history()), 2)

    async def test_skip_with_no_next_song_explains_empty_or_paused_queue(self):
        self.service.advance_queue.return_value = False
        await self.handle(tokens=51)
        self.assertIn('No next song started', self.notice())
        self.assertIn('empty or paused', self.notice())
        self.assertIn('before tipping again', self.notice())

    async def test_youtube_search_uses_shared_extraction(self):
        self.service._active_source.return_value = 'youtube'
        await self.handle()
        self.actions.extract_song_titles.assert_awaited_once()
        self.service.search_youtube_tracks.assert_awaited_once_with('Adele - Hello', limit=1)
        self.assertEqual(self.history()[0]['resolved_uri'], 'https://youtu.be/abc')
        self.actions.find_song_spotify.assert_not_called()
        self.actions.spotify_unavailability.assert_not_called()

    async def test_youtube_no_song_guidance_uses_youtube_links(self):
        self.service._active_source.return_value = 'youtube'
        self.actions.extract_song_titles.return_value = []
        await self.handle('thank you')
        self.assertIn('YouTube link in chat', self.notice())
        self.assertNotIn('Spotify', self.notice())

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

    async def test_availability_checks_market_playability_and_restriction_reasons(self):
        spotify = Mock()
        spotify.me.return_value = {'country': 'CA'}
        actions = SimpleNamespace(chatdj_enabled=True, auto_dj=SimpleNamespace(spotify=spotify))
        for info, expected in [({'uri': 'track', 'is_playable': True}, None),
                               ({'uri': 'track', 'is_playable': False}, 'unavailable'),
                               ({'uri': 'track', 'available_markets': []}, 'market'),
                               ({'uri': 'track', 'available_markets': [], 'is_playable': True}, None),
                               ({'uri': 'track', 'available_markets': ['US']}, 'market'),
                               ({'uri': 'track', 'available_markets': ['CA']}, None),
                               ({'uri': 'track', 'is_local': True}, 'local'),
                               *[({'uri': 'track', 'restrictions': {'reason': reason}}, reason)
                                 for reason in ('market', 'explicit', 'product')],
                               ({'uri': 'track', 'restrictions': {'reason': 'future_reason'},
                                 'available_markets': []}, 'unavailable')]:
            with self.subTest(info=info):
                spotify.track.return_value = info
                self.assertEqual(await self.Actions.spotify_unavailability(actions, 'track'), expected)
                spotify.track.assert_called_with('track', market='CA')

    async def test_availability_failures_do_not_claim_a_region_block(self):
        spotify = Mock()
        spotify.me.return_value = {'country': 'CA'}
        actions = SimpleNamespace(chatdj_enabled=True, auto_dj=SimpleNamespace(spotify=spotify))
        spotify.track.side_effect = RuntimeError('lookup failed')
        with self.assertRaises(SongResolutionError):
            await self.Actions.spotify_unavailability(actions, 'track')
        spotify.track.side_effect = None
        for track in (None, {}, []):
            spotify.track.return_value = track
            with self.subTest(track=track), self.assertRaises(SongResolutionError):
                await self.Actions.spotify_unavailability(actions, 'track')
        spotify.track.reset_mock()
        for profile in (None, {}, {'country': 'USA'}, {'country': None}, {'country': '12'}):
            spotify.me.return_value = profile
            with self.subTest(profile=profile), self.assertRaises(SongResolutionError):
                await self.Actions.spotify_unavailability(actions, 'track')
        spotify.me.side_effect = TimeoutError('profile failed')
        with self.assertRaises(SongResolutionError):
            await self.Actions.spotify_unavailability(actions, 'track')
        spotify.track.assert_not_called()
        for actions in (SimpleNamespace(chatdj_enabled=False), SimpleNamespace(chatdj_enabled=True)):
            with self.assertRaises(SongResolutionError):
                await self.Actions.spotify_unavailability(actions, 'track')


if __name__ == '__main__':
    unittest.main()
