import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import Mock


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('request_evaluation', ROOT/'scripts/evaluate_song_requests.py')
evaluation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluation)


class EvaluationTests(unittest.TestCase):
    def test_fixture_count_and_unique_ids(self):
        cases = json.loads((ROOT/'tests/fixtures/song_requests.json').read_text(encoding='utf-8'))
        self.assertGreaterEqual(len(cases), 25)
        self.assertEqual(len(cases), len({c['id'] for c in cases}))
        for case in cases:
            self.assertLessEqual(len(case['expected']), case['count'])
            for expected in case['expected']:
                self.assertTrue(expected['titles'])
                self.assertTrue(expected['acceptable_tracks'])

    def test_wrapper_exposes_no_playback_methods(self):
        client = Mock()
        wrapper = evaluation.ReadOnlySpotify(client)
        for name in ('start_playback', 'pause_playback', 'next_track', 'add_to_queue'):
            self.assertFalse(hasattr(wrapper, name))
        wrapper.search(q='song')
        client.search.assert_called_once_with(q='song')

    def test_empty_gold_requires_no_extraction_or_resolution(self):
        case = {'expected': []}
        self.assertEqual(evaluation.score([], [], case),
                         {'extraction_correct': True, 'catalog_correct': True})
        self.assertEqual(evaluation.score([{'song': 'thanks', 'artist': ''}], [None], case),
                         {'extraction_correct': False, 'catalog_correct': False})

    def test_errors_never_include_exception_text(self):
        result = evaluation.safe_error(RuntimeError('secret request URL or key'))
        self.assertNotIn('secret', json.dumps(result))


if __name__ == '__main__':
    unittest.main()
