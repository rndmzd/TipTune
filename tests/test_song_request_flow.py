import importlib
import logging
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from chatdj.song_requests import SongRequest, SongExtractionError, SongResolutionError


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
        self.actions = SimpleNamespace(
            extract_song_titles=AsyncMock(return_value=[SongRequest(song='Hello', artist='Adele')]),
            find_song_spotify=AsyncMock(return_value='spotify:track:'+'A'*22),
            available_in_market=AsyncMock(return_value=True),
            trigger_warning_overlay=AsyncMock(), trigger_song_requester_overlay=AsyncMock())
        self.service = SimpleNamespace(
            checks=SimpleNamespace(is_song_request=Mock(return_value=True),
                is_skip_song_request=Mock(return_value=False), get_request_count=Mock(return_value=2)),
            actions=self.actions, _active_source=Mock(return_value='spotify'),
            _allow_source_override_in_request_message=Mock(return_value=False),
            _youtube_url_from_text=Mock(return_value=None),
            search_youtube_tracks=AsyncMock(return_value=[{'uri': 'https://youtu.be/abc'}]),
            publish_request_history_item=Mock(), add_track_to_queue=AsyncMock(return_value=True),
            _get_obs_overlay_duration_seconds=Mock(return_value=10))

    async def handle(self, message='Hello by Adele'):
        await self.app.SongRequestService._handle_tip(self.service,
            {'tip': {'tokens': 100, 'message': message}, 'user': {'username': 'Test'}})

    def history(self):
        return [call.args[0] for call in self.service.publish_request_history_item.call_args_list]

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
        self.actions.find_song_spotify.assert_not_called()

    async def test_short_title_is_unmodified(self):
        await self.handle('U')
        self.actions.extract_song_titles.assert_awaited_once_with('U', 2)

    async def test_partial_resolution_survives_exception(self):
        self.actions.extract_song_titles.return_value = [SongRequest(song='One', artist=''),
                                                        SongRequest(song='Two', artist='')]
        self.actions.find_song_spotify.side_effect = [SongResolutionError('failed'), 'spotify:track:'+'B'*22]
        await self.handle('One; Two')
        self.assertEqual([h['status'] for h in self.history()], ['failed', 'added'])
        self.assertEqual(self.history()[0]['error'], 'spotify lookup failed')
        self.service.add_track_to_queue.assert_awaited_once()

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
