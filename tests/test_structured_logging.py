import io
import json
import logging
import unittest

from utils.structured_logging import StructuredLogFormatter, StructuredLogger


class StructuredLoggingTests(unittest.TestCase):
    def setUp(self):
        self.output = io.StringIO()
        handler = logging.StreamHandler(self.output)
        handler.setFormatter(StructuredLogFormatter())
        self.logger = logging.Logger('test', level=logging.DEBUG)
        self.logger.addHandler(handler)

    def test_event_and_error_message_survive_formatting(self):
        StructuredLogger(self.logger).exception(
            'spotify.playback.start.error', exc=ValueError('Restriction violated'),
            message='Playback failed', data={'track_uri': 'spotify:track:test'},
        )
        entry = json.loads(self.output.getvalue())
        self.assertEqual(entry['event_type'], 'spotify.playback.start.error')
        self.assertEqual(entry['message'], 'Playback failed')
        self.assertEqual(entry['error']['message'], 'Restriction violated')

    def test_spotipy_header_is_redacted(self):
        self.logger.debug('Sending request Headers: %s', {'Authorization': 'Bearer dummy-token_123'})
        entry = json.loads(self.output.getvalue())
        self.assertNotIn('dummy-token_123', entry['message'])
        self.assertIn('Bearer [REDACTED]', entry['message'])


if __name__ == '__main__':
    unittest.main()
