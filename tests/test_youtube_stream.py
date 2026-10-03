import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from app import _yt_dlp_dump_json


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


if __name__ == '__main__':
    unittest.main()
