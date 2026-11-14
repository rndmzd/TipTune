import subprocess
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from aiohttp import web

from app import SongRequestService, _yt_dlp_dump_json


VIDEO_URL = 'https://www.youtube.com/watch?v=lj3iNxZ8Dww'


def audio_format(format_id, language='en', **fields):
    return {
        'format_id': format_id,
        'url': f'https://media.example/{format_id}',
        'language': language,
        'vcodec': 'none',
        'acodec': 'mp4a.40.2',
        'ext': 'm4a',
        'protocol': 'https',
        'abr': 128,
        **fields,
    }


class YoutubeExtractorTests(unittest.TestCase):
    def extract(self, bundled, on_path):
        with patch('app._yt_dlp_bin_path', return_value='yt-dlp'), \
             patch('app.find_bundled_bin_path', side_effect=bundled), \
             patch('app.shutil.which', side_effect=on_path), \
             patch('app.subprocess.run', return_value=subprocess.CompletedProcess(
                 [], 0, stdout='{"title":"Test"}\n', stderr='')) as run:
            self.assertEqual(_yt_dlp_dump_json(['--no-playlist', 'https://youtu.be/test']),
                             [{'title': 'Test'}])
            return run.call_args.args[0]

    def test_bundled_deno_works_without_system_runtime(self):
        runtime_path = Path('/TipTune resources/deno')
        command = self.extract(
            lambda name: runtime_path if name == 'deno' else None,
            lambda name: None)
        index = command.index('--js-runtimes')
        self.assertEqual(command[index + 1], f'deno:{runtime_path}')
        self.assertEqual(command[-2:], ['--no-playlist', 'https://youtu.be/test'])

    def test_system_deno_is_enabled(self):
        command = self.extract(lambda name: None,
                               lambda name: '/usr/bin/deno' if name == 'deno' else None)
        self.assertIn('deno:/usr/bin/deno', command)

    def test_node_fallback_is_explicitly_enabled(self):
        command = self.extract(lambda name: None,
                               lambda name: '/usr/bin/node' if name == 'node' else None)
        self.assertIn('node:/usr/bin/node', command)

    def test_flat_search_still_works_without_runtime(self):
        command = self.extract(lambda name: None, lambda name: None)
        self.assertNotIn('--js-runtimes', command)


class YoutubeAudioSelectionTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch('app.logger'))

    def fetch(self, formats=None, **info):
        if formats is not None:
            info['formats'] = formats
        service = SongRequestService.__new__(SongRequestService)
        with patch('app._yt_dlp_dump_json', return_value=[info]):
            return service._yt_fetch_best_audio_url(VIDEO_URL)

    def test_reported_clip_uses_original_english_despite_arabic_bitrate(self):
        # Language/bitrate metadata from lj3iNxZ8Dww. The old picker chose
        # Arabic for a 0.001 kbps advantage over the original English track.
        arabic = audio_format('140-0', 'ar', abr=129.598,
                              language_preference=-1, format_note='Arabic, medium')
        english = audio_format('140-20', 'en-US', abr=129.597,
                               language_preference=10,
                               format_note='English (US) original (default), medium')
        for formats in ([arabic, english], [english, arabic]):
            with self.subTest(order=[item['format_id'] for item in formats]):
                self.assertEqual(self.fetch(formats)[0], english['url'])

    def test_accepts_english_language_variants(self):
        for language in ('en', 'en-US', 'en-GB', 'en-orig', 'EN_us', 'eng'):
            with self.subTest(language=language):
                english = audio_format('english', language, abr=96)
                foreign = audio_format('foreign', 'ar', abr=192)
                self.assertEqual(self.fetch([foreign, english])[0], english['url'])

    def test_english_webm_beats_foreign_m4a(self):
        english = audio_format('english', 'en', ext='webm', acodec='opus', abr=96)
        foreign = audio_format('foreign', 'ar', abr=192)
        self.assertEqual(self.fetch([foreign, english])[:2],
                         (english['url'], 'audio/webm'))

    def test_english_streaming_fallback_beats_foreign_direct_audio(self):
        for protocol in ('m3u8_native', 'http_dash_segments'):
            with self.subTest(protocol=protocol):
                english = audio_format('english', 'en', protocol=protocol, abr=96)
                foreign = audio_format('foreign', 'ar', abr=192)
                self.assertEqual(self.fetch([foreign, english])[0], english['url'])

    def test_english_beats_untagged_audio(self):
        english = audio_format('english', 'en', abr=96)
        untagged = audio_format('untagged', None, abr=192)
        self.assertEqual(self.fetch([untagged, english])[0], english['url'])

    def test_original_english_beats_english_audio_description(self):
        original = audio_format('original', 'en', abr=96, language_preference=10)
        description = audio_format('description', 'en', abr=192,
                                   language_preference=-10)
        self.assertEqual(self.fetch([description, original])[0], original['url'])

    def test_foreign_only_audio_is_rejected(self):
        formats = [audio_format('arabic', 'ar'), audio_format('french', 'fr-FR')]
        with self.assertRaisesRegex(RuntimeError, 'English audio is not available'):
            self.fetch(formats)

    def test_untagged_audio_cannot_bypass_missing_english_in_multilingual_clip(self):
        formats = [audio_format('arabic', 'ar'), audio_format('untagged', None)]
        with self.assertRaisesRegex(RuntimeError, 'English audio is not available'):
            self.fetch(formats, **audio_format('selected', None))

    def test_clips_without_language_metadata_still_play(self):
        for language in (None, '', 'und', 'zxx'):
            with self.subTest(language=language):
                low = audio_format('low', language, abr=64)
                high = audio_format('high', language, abr=128)
                self.assertEqual(self.fetch([low, high])[0], high['url'])

    def test_compatible_direct_english_format_is_preferred(self):
        m4a = audio_format('m4a', 'en', abr=96)
        webm = audio_format('webm', 'en', ext='webm', acodec='opus', abr=192)
        manifest = audio_format('manifest', 'en', protocol='m3u8_native', abr=256)
        self.assertEqual(self.fetch([webm, manifest, m4a])[0], m4a['url'])

    def test_ignores_video_silent_and_missing_url_formats(self):
        english = audio_format('english', 'en', ext='webm', acodec='opus', abr=96)
        invalid = [None, audio_format('video', 'en', vcodec='avc1', abr=256),
                   audio_format('silent', 'en', acodec='none'),
                   audio_format('missing', 'en', url='')]
        self.assertEqual(self.fetch([*invalid, english])[0], english['url'])

    def test_video_only_english_cannot_allow_a_foreign_audio_fallback(self):
        formats = [audio_format('video', 'en', vcodec='avc1', acodec='none'),
                   audio_format('arabic', 'ar')]
        with self.assertRaisesRegex(RuntimeError, 'English audio is not available'):
            self.fetch(formats)

    def test_does_not_fall_back_to_first_non_audio_format(self):
        with self.assertRaisesRegex(RuntimeError, 'No audio stream URL found'):
            self.fetch([audio_format('video', 'en', vcodec='avc1', acodec='none')])

    def test_top_level_selected_format_preserves_language_mime_and_headers(self):
        english = audio_format('selected', 'en-GB', mime_type='audio/mp4; codecs="mp4a"',
                               http_headers={'User-Agent': 'selected-agent'})
        self.assertEqual(self.fetch(**english),
                         (english['url'], 'audio/mp4', {'User-Agent': 'selected-agent'}))

    def test_top_level_foreign_format_is_rejected(self):
        for formats in (None, []):
            with self.subTest(formats=formats):
                with self.assertRaisesRegex(RuntimeError, 'English audio is not available'):
                    self.fetch(formats, **audio_format('selected', 'ar'))

    def test_top_level_foreign_selection_cannot_override_english_formats(self):
        english = audio_format('english', 'en', abr=96)
        self.assertEqual(self.fetch([english], **audio_format('selected', 'ar', abr=192))[0],
                         english['url'])

    def test_selected_format_headers_override_info_headers(self):
        english = audio_format('english', 'en', http_headers={'User-Agent': 'english-agent'})
        foreign = audio_format('foreign', 'ar', abr=192,
                               http_headers={'User-Agent': 'foreign-agent'})
        result = self.fetch([foreign, english], http_headers={'User-Agent': 'default-agent'})
        self.assertEqual(result, (english['url'], 'audio/mp4', {'User-Agent': 'english-agent'}))

    def test_info_headers_are_used_when_selected_format_has_none(self):
        english = audio_format('english', 'en')
        result = self.fetch([english], http_headers={'User-Agent': 'default-agent'})
        self.assertEqual(result[2], {'User-Agent': 'default-agent'})


class YoutubeStreamLanguageTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_english_is_reported_without_streaming_another_language(self):
        service = SongRequestService.__new__(SongRequestService)
        info = {'formats': [audio_format('arabic', 'ar')]}
        with patch('app._yt_dlp_dump_json', return_value=[info]), \
             patch('app.ClientSession') as upstream, patch('app.logger'):
            with self.assertRaises(web.HTTPBadRequest) as raised:
                await service.stream_youtube_audio(VIDEO_URL, SimpleNamespace(headers={}))
        self.assertIn('English audio is not available', raised.exception.text)
        upstream.assert_not_called()


if __name__ == '__main__':
    unittest.main()
