import json
import unittest

from utils.song_extraction_debug import extraction_exception_debug, sanitize_extraction_debug


class ExtractionDebugTests(unittest.TestCase):
    def test_nested_secrets_and_unlabelled_configured_credentials_are_redacted(self):
        data = {'body': {'authorization': 'Bearer private-bearer', 'api_key': 'private-key',
                         'access_token': 'private-token', 'password': 'private-password'},
                'message': 'Invalid private-configured-key; Bearer abcdef; sk-proj-test-secret',
                'url': 'https://example.test/?key=query-secret&access_token=token-secret',
                'output': '{"api_key":"json-secret"}'}
        sanitized = json.dumps(sanitize_extraction_debug(data, ['private-configured-key']))
        for secret in ('private-bearer', 'private-key', 'private-token', 'private-password',
                       'private-configured-key', 'abcdef', 'sk-proj-test-secret', 'query-secret',
                       'token-secret', 'json-secret'):
            self.assertNotIn(secret, sanitized)

    def test_large_provider_messages_are_bounded_and_marked(self):
        debug = sanitize_extraction_debug({'output': 'a' * 20000, 'items': list(range(100))})
        self.assertTrue(debug['output'].endswith('[truncated]'))
        self.assertEqual(debug['items'][-1], '[truncated]')
        self.assertLess(len(json.dumps(debug)), 17000)

    def test_diagnostic_serialization_failure_does_not_replace_the_original_failure(self):
        class Unserializable:
            def model_dump(self, **kwargs):
                raise ValueError('Cannot serialize')
        debug = sanitize_extraction_debug({'output': Unserializable(), 'exception': 'Original failure'})
        self.assertIn('serialization unavailable', debug['output'])
        self.assertEqual(debug['exception'], 'Original failure')

    def test_traceback_captures_the_cause_without_local_variable_values(self):
        try:
            secret_local = 'never-include-local-value'
            try:
                raise TimeoutError('Provider timed out')
            except TimeoutError as cause:
                raise RuntimeError('Extraction failed') from cause
        except RuntimeError as exc:
            debug = extraction_exception_debug(exc)
        self.assertEqual([item['type'] for item in debug['chain']], ['RuntimeError', 'TimeoutError'])
        self.assertIn('Provider timed out', debug['traceback'])
        self.assertNotIn(secret_local, json.dumps(debug))
