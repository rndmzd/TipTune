import asyncio
import configparser
import io
import json
import logging
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import httpx

from utils.build_info import create_build_info
from utils.error_reporting import ErrorReporter, sanitize_report
from utils.structured_logging import StructuredLogger, StructuredLogFormatter
from utils.support_diagnostics import current_support_id, environment_for_report, sanitize_environment, valid_support_id


class SupportDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.storage = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(self.storage)
        self.config_path = self.root / 'config.ini'
        self.config_path.write_text('[General]\nsend_error_reports=true\n[Music]\nsource=spotify\n'
                                    '[OBS]\nenabled=false\npassword=private-test-secret')
        self.post = self.enterContext(patch('utils.error_reporting.httpx.post', return_value=httpx.Response(200)))
        self.reporter = self.make_reporter()

    def make_reporter(self):
        reporter = ErrorReporter(self.config_path, self.root / 'cache', 'https://public@example.org/1',
                                 start_worker=False, traces_sample_rate=1.0)
        self.addCleanup(reporter.close)
        return reporter

    def errors(self):
        return [event for path in self.reporter.pending_dir.glob('*.json')
                if (event := json.loads(path.read_text())['event']).get('type') != 'transaction']

    def test_reporting_off_keeps_a_local_support_id_without_collecting_or_sending_environment(self):
        self.config_path.write_text('[General]\nsend_error_reports=false')
        self.reporter.configure()
        with patch('utils.error_reporting.environment_for_report') as environment:
            with self.reporter.operation():
                self.reporter.capture_failure('song.queue.failed', source='spotify')
                support = self.reporter.support_details()
                self.assertTrue(valid_support_id(support['support_id']))
                self.assertEqual(support['report_state'], 'disabled')
                self.assertNotIn('report_id', support)
            environment.assert_not_called()
        self.assertIsNone(self.reporter._session_id)
        self.assertFalse(self.reporter.pending_dir.exists())
        self.post.assert_not_called()

    def test_functional_failure_has_searchable_code_support_id_and_matching_trace(self):
        with self.reporter.operation(), self.reporter.trace('song.request'):
            event_id = self.reporter.capture_failure('song.lookup.failed', TimeoutError('private-test-secret'),
                                                    source='youtube', stage='track_lookup')
            support = self.reporter.support_details()
        event = self.errors()[0]
        self.assertEqual(event['event_id'], event_id)
        self.assertEqual(event['tags']['support_id'], support['support_id'])
        self.assertEqual(event['tags']['error_code'], 'song.lookup.failed')
        self.assertEqual(event['contexts']['trace']['trace_id'], support['trace_id'])
        self.assertEqual(event['contexts']['failure']['source'], 'youtube')
        self.assertEqual(support['report_state'], 'queued')
        self.assertNotIn('private-test-secret', json.dumps(event))
        self.post.assert_not_called()

    def test_duplicates_are_suppressed_within_an_operation_but_other_requests_get_their_own_reports(self):
        with self.reporter.operation():
            first = self.reporter.capture_failure('song.queue.failed', source='spotify')
            support = self.reporter.support_details()
            self.assertIsNone(self.reporter.capture_failure('song.queue.failed', source='spotify'))
            self.assertEqual(self.reporter.support_details()['report_id'], first)
        with self.reporter.operation():
            self.assertIsNotNone(self.reporter.capture_failure('song.queue.failed', source='spotify'))
            self.assertNotEqual(self.reporter.support_details()['support_id'], support['support_id'])
        self.assertEqual(len(self.errors()), 2)

    def test_environment_is_an_allowlist_and_original_snapshot_survives_replay(self):
        snapshot = {'app_version': '1.8.1', 'python_version': '3.13.11', 'architecture': 'x86_64',
                    'build_id': 'a' * 64, 'commit': 'b' * 40, 'source_dirty': False,
                    'dependencies': {'openai': '3.19.2', 'private-package': '1.2.3'},
                    'music_source': 'spotify', 'username': 'private-user',
                    'request': {'body': 'private-body'}, 'hostname': 'private-machine'}
        with patch('utils.error_reporting.environment_for_report', return_value=snapshot):
            with self.reporter.operation():
                self.reporter.capture_failure('song.queue.failed', source='spotify')
        original = self.errors()[0]['contexts']['diagnostics']
        self.assertNotIn('private', json.dumps(self.errors()))
        self.post.side_effect = httpx.ConnectError('offline')
        self.reporter.process_pending()
        restored = self.make_reporter()
        self.post.reset_mock(side_effect=True)
        with patch('utils.error_reporting.environment_for_report', return_value={'app_version': '9.0.0'}) as collect:
            with patch('utils.error_reporting.time.time', return_value=time.time() + 4000):
                restored.process_pending()
            collect.assert_not_called()
        uploaded = json.loads(self.post.call_args.kwargs['content'].split(b'\n', 2)[2])
        self.assertEqual(uploaded['contexts']['diagnostics'], original)
        self.assertEqual(sanitize_report(sanitize_report(uploaded)), uploaded)

    def test_session_id_rotates_after_opt_out_and_reenable(self):
        first = self.reporter._session_id
        self.config_path.write_text('[General]\nsend_error_reports=false')
        self.reporter.configure()
        self.assertIsNone(self.reporter._session_id)
        self.config_path.write_text('[General]\nsend_error_reports=true')
        self.reporter.configure()
        self.assertNotEqual(first, self.reporter._session_id)

    def test_configured_credentials_are_removed_from_environment_fields(self):
        self.config_path.write_text('[General]\nsend_error_reports=true\n[OBS]\npassword=1.2.3')
        with patch('utils.error_reporting.environment_for_report', return_value={
                'python_version': '1.2.3', 'dependencies': {'openai': '1.2.3'}}):
            self.reporter.capture_failure('song.queue.failed')
        self.assertNotIn('1.2.3', json.dumps(self.errors()))
        self.reporter.process_pending()
        self.assertNotIn('1.2.3', self.post.call_args.kwargs['content'].decode())

    def test_support_id_is_present_in_structured_logs(self):
        output = io.StringIO()
        handler = logging.StreamHandler(output)
        handler.setFormatter(StructuredLogFormatter())
        logger = logging.Logger('support-test', level=logging.INFO)
        logger.addHandler(handler)
        with self.reporter.operation() as operation:
            StructuredLogger(logger).info('song.lookup.started')
        self.assertEqual(json.loads(output.getvalue())['support_id'], operation.support_id)
        self.assertIsNone(current_support_id())

    def test_environment_rejects_free_text_and_hardware_identifiers(self):
        value = sanitize_environment({'python_version': '123-private-user', 'os_build': '11.0.12345',
                                      'architecture': 'private-device', 'session_id': 'private',
                                      'serial': 'private-hardware', 'obs_enabled': 'private',
                                      'dependencies': {'openai': '123-private-token'}})
        self.assertEqual(value, {'os_build': '11.0.12345', 'dependencies': {}})

    def test_missing_configuration_flags_are_omitted_instead_of_guessing_runtime_defaults(self):
        config = configparser.ConfigParser()
        config.read_dict({'OBS': {'password': 'private-secret'}})
        with patch('utils.support_diagnostics.runtime_environment', return_value={}):
            snapshot = environment_for_report(config, '1.8.1', 'a' * 32)
        self.assertNotIn('obs_enabled', snapshot)
        self.assertNotIn('music_source', snapshot)
        self.assertNotIn('overlay_mode', snapshot)

    def test_build_hash_covers_code_and_excludes_user_configuration(self):
        (self.root / 'app.py').write_text('print("application")')
        (self.root / 'utils').mkdir()
        (self.root / 'utils' / 'feature.py').write_text('VALUE = 1')
        def git(args, **kwargs):
            return SimpleNamespace(stdout='a' * 40 if args[1] == 'rev-parse' else ' M app.py')
        with patch('utils.build_info.subprocess.run', side_effect=git):
            before = create_build_info(self.root)
            self.config_path.write_text('private-config-change')
            self.assertEqual(before, create_build_info(self.root))
            (self.root / 'utils' / 'feature.py').write_text('VALUE = 2')
            after = create_build_info(self.root)
        self.assertNotEqual(before['build_id'], after['build_id'])
        self.assertEqual(before['commit'], 'a' * 40)
        self.assertTrue(before['source_dirty'])
        self.assertNotIn('private', json.dumps(before))


class SupportContextTests(unittest.IsolatedAsyncioTestCase):
    async def test_threaded_work_keeps_support_id_and_finished_background_context_gets_a_new_one(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            reporter = ErrorReporter(root / 'config.ini', root / 'cache', '', start_worker=False)
            self.addCleanup(reporter.close)
            ready = asyncio.Event()

            async def background():
                await ready.wait()
                self.assertIsNone(current_support_id())
                with reporter.operation() as operation:
                    return operation.support_id

            with reporter.operation() as operation:
                original_id = operation.support_id
                self.assertEqual(await asyncio.to_thread(current_support_id), original_id)
                task = asyncio.create_task(background())
            ready.set()
            self.assertNotEqual(await asyncio.wait_for(task, 2), original_id)
