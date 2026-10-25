import configparser
import asyncio
import importlib
import json
import logging
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, Mock, patch

import httpx
import sentry_sdk
from aiohttp.test_utils import TestClient, TestServer

from utils.error_reporting import ErrorReporter, ErrorReportingHandler, MAX_AGE_SECONDS, MAX_REPORTS, sanitize_report
from utils.structured_logging import StructuredLogger


DSN = 'https://0123456789abcdef0123456789abcdef@o123.ingest.sentry.io/456'


def envelope_event(content):
    header, item, event = content.split(b'\n', 2)
    assert json.loads(item)['type'] == 'event'
    return json.loads(event)


class ErrorReportingTests(unittest.TestCase):
    def setUp(self):
        self.storage = tempfile.TemporaryDirectory()
        self.root = Path(self.storage.name)
        self.config_path = self.root / 'config.ini'
        self.post = self.enterContext(patch('utils.error_reporting.httpx.post', return_value=httpx.Response(200)))
        self.reporters = []

    def tearDown(self):
        for reporter in self.reporters:
            reporter.close()
        self.storage.cleanup()

    def consent(self, enabled):
        self.config_path.write_text('[General]\nsend_error_reports=' + str(enabled).lower(), encoding='utf-8')

    def reporter(self, dsn=DSN, **options):
        reporter = ErrorReporter(self.config_path, self.root / 'cache', dsn, environment='test', start_worker=False, **options)
        self.reporters.append(reporter)
        return reporter

    def test_off_by_default_creates_no_client_queue_or_network_request(self):
        with patch('utils.error_reporting.sentry_sdk.Client') as client:
            reporter = self.reporter()
            self.assertIsNone(reporter.capture_exception('app.error', ValueError('private text')))
            self.assertIsNone(reporter.capture_frontend({'release': 'tiptune@1.4.3', 'frames': [
                {'filename': 'app-example.js', 'abs_path': 'app:///assets/app-example.js',
                 'debug_id': '01234567-89ab-4cde-8f01-23456789abcd', 'lineno': 1, 'colno': 100},
            ]}))
            reporter.breadcrumb('app.started')
            with reporter.trace('song.request') as span:
                self.assertIsNone(span)
            reporter.process_pending()
            self.assertFalse(reporter.status()['enabled'])
            self.assertFalse(reporter.pending_dir.exists())
            self.assertIsNone(reporter._thread)
            client.assert_not_called()
            self.post.assert_not_called()

    def test_missing_or_invalid_destination_never_starts_reporting(self):
        self.consent(True)
        for dsn in ('', 'http://key@example.org/1', 'https://public:private@example.org/1', 'bad dsn'):
            reporter = self.reporter(dsn)
            self.assertFalse(reporter.status()['configured'])
            self.assertIsNone(reporter.capture_exception('app.error', ValueError('private')))
            reporter.process_pending()
        self.post.assert_not_called()

    def test_exception_report_preserves_stack_and_api_metadata_without_private_data(self):
        self.consent(True)
        reporter = self.reporter()
        reporter.breadcrumb('song.extract.started')
        private_local = 'private-user private-tip sk-private-token https://events.example/private-secret'
        try:
            raise RuntimeError(private_local)
        except RuntimeError as cause:
            try:
                exc = ValueError(private_local)
                exc.status_code = 429
                exc.request_id = 'req_example_123'
                exc.body = {'message': private_local}
                raise exc from cause
            except ValueError as error:
                event_id = reporter.capture_exception('song.extract.failed', error, details={
                    'stage': 'model_request', 'source': 'spotify', 'username': 'private-user',
                    'tip_message': 'private-tip', 'model_response': private_local, 'headers': {'Authorization': private_local},
                })
        stored = next(reporter.pending_dir.glob('*.json')).read_text()
        reporter.process_pending()
        uploaded = self.post.call_args.kwargs['content']
        for text in ('private-user', 'private-tip', 'sk-private-token', 'private-secret', 'private_local', str(self.root), 'Authorization'):
            self.assertNotIn(text, stored)
            self.assertNotIn(text, uploaded.decode())
        event = envelope_event(uploaded)
        self.assertEqual(event['event_id'], event_id)
        self.assertEqual([e['type'] for e in event['exception']['values']], ['RuntimeError', 'ValueError'])
        self.assertTrue(event['exception']['values'][-1]['stacktrace']['frames'])
        self.assertEqual(event['contexts']['failure'], {'stage': 'model_request', 'source': 'spotify', 'status_code': 429, 'request_id': 'req_example_123'})
        self.assertNotIn('user', event)
        self.assertNotIn('request', event)
        self.assertNotIn('server_name', event)
        self.assertNotIn('modules', event)
        self.assertEqual(reporter.status()['pending'], 0)
        self.assertTrue(reporter.status()['last_sent_at'])

    def test_structured_logged_exception_keeps_real_traceback_and_only_enqueues(self):
        self.consent(True)
        reporter = self.reporter()
        logger = logging.Logger('isolated', level=logging.INFO)
        logger.addHandler(ErrorReportingHandler(reporter))
        try:
            raise ValueError('private request body')
        except ValueError as exc:
            StructuredLogger(logger).exception('spotify.playback.error', exc=exc, message='private username')
        self.post.assert_not_called()
        record = json.loads(next(reporter.pending_dir.glob('*.json')).read_text())
        self.assertEqual(record['event']['exception']['values'][0]['type'], 'ValueError')
        self.assertIn('lineno', record['event']['exception']['values'][0]['stacktrace']['frames'][-1])

    def test_offline_report_replays_after_restart_and_keeps_its_original_release(self):
        self.consent(True)
        reporter = self.reporter()
        reporter.release = 'tiptune@1.2.3'
        event_id = reporter.capture_exception('app.error', RuntimeError('private'))
        self.post.side_effect = httpx.ConnectError('offline')
        reporter.process_pending()
        self.assertEqual(reporter.status()['pending'], 1)
        restored = self.reporter()
        self.post.side_effect = None
        with patch('utils.error_reporting.time.time', return_value=time.time() + 4000):
            restored.process_pending()
        event = envelope_event(self.post.call_args.kwargs['content'])
        self.assertEqual(event['event_id'], event_id)
        self.assertEqual(event['release'], 'tiptune@1.2.3')
        self.assertEqual(restored.status()['pending'], 0)

    def test_revoking_consent_discards_unsent_reports_without_sending(self):
        self.consent(True)
        reporter = self.reporter()
        reporter.capture_exception('app.error', RuntimeError())
        reporter.capture_exception('app.another.error', ValueError())
        self.consent(False)
        reporter.process_pending()  # Also checks durable consent, even without a configure call.
        self.assertEqual(reporter.status()['pending'], 0)
        self.post.assert_not_called()
        self.consent(True)
        reporter.configure()
        reporter.process_pending()
        self.post.assert_not_called()

    def test_rate_limit_retry_is_delayed_and_failed_delivery_is_not_reported_as_sent(self):
        self.consent(True)
        reporter = self.reporter()
        reporter.capture_exception('app.error', RuntimeError())
        reporter.capture_exception('app.another.error', ValueError())
        self.post.return_value = httpx.Response(429, headers={'Retry-After': '120'})
        reporter.process_pending()
        reporter.process_pending()
        self.assertEqual(self.post.call_count, 1)
        records = [json.loads(path.read_text()) for path in reporter.pending_dir.glob('*.json')]
        record = next(record for record in records if record['attempts'] > 0)
        self.assertGreaterEqual(record['retry_at'] - time.time(), 118)
        self.assertIsNone(reporter.last_sent_at)
        self.post.return_value = httpx.Response(403)
        with patch('utils.error_reporting.time.time', return_value=time.time() + 4000):
            reporter.process_pending()
        self.assertEqual(reporter.status()['pending'], 0)
        self.assertIsNone(reporter.last_sent_at)
        self.assertIn('rejected', reporter.delivery_error)

    def test_repeated_failures_and_total_queue_are_bounded(self):
        self.consent(True)
        reporter = self.reporter()
        self.assertIsNotNone(reporter.capture_exception('app.error', RuntimeError()))
        self.assertIsNone(reporter.capture_exception('app.error', RuntimeError()))
        for i in range(MAX_REPORTS + 10):
            reporter.capture_exception('app.error' + str(i), RuntimeError())
        self.assertLessEqual(reporter.status()['pending'], MAX_REPORTS)

    def test_expired_reports_are_deleted_without_uploading(self):
        self.consent(True)
        reporter = self.reporter()
        reporter.capture_exception('app.error', RuntimeError())
        with patch('utils.error_reporting.time.time', return_value=time.time() + MAX_AGE_SECONDS + 1):
            reporter.process_pending()
        self.assertEqual(reporter.status()['pending'], 0)
        self.post.assert_not_called()

    def test_frontend_event_drops_messages_locals_paths_and_unrecognized_fields(self):
        event = sanitize_report({'extra': {'secret': 'private'}, 'request': {'data': 'private'},
            'tags': {'component': 'webui', 'event_type': 'webui.unhandled.error', 'user': 'private'},
            'exception': {'values': [{'type': 'TypeError', 'value': 'private username', 'stacktrace': {'frames': [
                {'filename': 'C:\\Users\\private-user\\assets\\app-example.js', 'lineno': 42, 'function': 'frontend',
                 'vars': {'token': 'private'}, 'pre_context': ['private'], 'context_line': 'private'},
            ]}}]}})
        self.assertNotIn('private', json.dumps(event))
        self.assertEqual(event['platform'], 'javascript')
        self.assertEqual(event['exception']['values'][0]['stacktrace']['frames'][0]['filename'], 'app-example.js')

    def test_configured_secret_in_allowed_metadata_is_redacted_before_storage_and_upload(self):
        self.config_path.write_text('[General]\nsend_error_reports=true\n[OBS]\npassword=ExampleSecretKey', encoding='utf-8')
        reporter = self.reporter()
        reporter.capture_exception('app.error', ValueError('private'), details={'request_id': 'ExampleSecretKey'})
        stored = next(reporter.pending_dir.glob('*.json')).read_text()
        self.assertNotIn('ExampleSecretKey', stored)
        reporter.process_pending()
        self.assertNotIn('ExampleSecretKey', self.post.call_args.kwargs['content'].decode())

    def test_frontend_source_map_metadata_and_compiled_release_survive_offline_replay(self):
        self.consent(True)
        reporter = self.reporter()
        reporter.release = 'tiptune@9.0.0'
        asset = 'app:///assets/app-example.js'
        debug_id = '01234567-89ab-4cde-8f01-23456789abcd'
        event_id = reporter.capture_frontend({'component': 'overlay', 'type': 'TypeError', 'release': 'tiptune@1.4.3',
            'message': 'private user request', 'frames': [
                {'filename': 'app-example.js', 'abs_path': asset, 'debug_id': debug_id, 'lineno': 2, 'colno': 345,
                 'function': 'private-user', 'vars': {'token': 'private'}, 'context_line': 'private source'},
            ]})
        stored = json.loads(next(reporter.pending_dir.glob('*.json')).read_text())['event']
        images = {'images': [{'type': 'sourcemap', 'code_file': asset, 'debug_id': debug_id}]}
        self.assertEqual(stored['debug_meta'], images)
        self.assertEqual(sanitize_report(sanitize_report(stored)), stored)
        self.post.side_effect = httpx.ConnectError('offline')
        reporter.process_pending()
        self.assertEqual(reporter.status()['pending'], 1)
        restored = self.reporter()
        self.post.side_effect = None
        with patch('utils.error_reporting.time.time', return_value=time.time() + 4000):
            restored.process_pending()
        event = envelope_event(self.post.call_args.kwargs['content'])
        self.assertEqual(event['event_id'], event_id)
        self.assertEqual(event['release'], 'tiptune@1.4.3')
        self.assertEqual(event['debug_meta'], images)
        self.assertEqual(event['exception']['values'][0]['stacktrace']['frames'], [
            {'filename': 'app-example.js', 'abs_path': asset, 'lineno': 2, 'colno': 345},
        ])
        self.assertNotIn('private', json.dumps(event))

    def test_source_map_images_must_match_canonical_stack_assets_and_valid_ids(self):
        debug_id = '01234567-89ab-4cde-8f01-23456789abcd'
        event = {'tags': {'component': 'webui'}, 'exception': {'values': [{'stacktrace': {'frames': [
            {'filename': 'app-valid.js', 'abs_path': 'app:///assets/app-valid.js', 'debug_id': debug_id},
            {'filename': 'app-foreign.js', 'abs_path': 'https://private.example/app-foreign.js?token=private', 'debug_id': debug_id},
            {'filename': 'app-wrong.js', 'abs_path': 'app:///assets/private.js', 'debug_id': debug_id},
            {'filename': 'app-bad-id.js', 'abs_path': 'app:///assets/app-bad-id.js', 'debug_id': 'private'},
        ]}}]}, 'debug_meta': {'images': [
            {'type': 'sourcemap', 'code_file': 'app:///assets/unused-private.js', 'debug_id': debug_id},
            {'type': 'sourcemap', 'code_file': 'file:///C:/private/app-valid.js', 'debug_id': debug_id},
            {'type': 'sourcemap', 'code_file': 'app:///assets/app-bad-id.js', 'debug_id': 'private'},
        ]}}
        safe = sanitize_report(event)
        self.assertEqual(safe['debug_meta']['images'], [
            {'type': 'sourcemap', 'code_file': 'app:///assets/app-valid.js', 'debug_id': debug_id},
        ])
        self.assertNotIn('private', json.dumps(safe))
        event['tags']['component'] = 'python'
        self.assertNotIn('debug_meta', sanitize_report(event))
        self.assertNotIn('abs_path', json.dumps(sanitize_report(event)))

    def test_configured_secrets_are_removed_from_source_map_metadata(self):
        self.config_path.write_text('[General]\nsend_error_reports=true\n[OBS]\npassword=ExampleSecretKey', encoding='utf-8')
        reporter = self.reporter()
        reporter.capture_frontend({'type': 'Error', 'frames': [
            {'filename': 'app-ExampleSecretKey.js', 'abs_path': 'app:///assets/app-ExampleSecretKey.js',
             'debug_id': '01234567-89ab-4cde-8f01-23456789abcd', 'lineno': 1},
        ]})
        stored = json.loads(next(reporter.pending_dir.glob('*.json')).read_text())['event']
        self.assertNotIn('ExampleSecretKey', json.dumps(stored))
        self.assertNotIn('debug_meta', stored)
        reporter.process_pending()
        self.assertNotIn('ExampleSecretKey', self.post.call_args.kwargs['content'].decode())

    def test_error_and_child_span_keep_original_trace_and_release_after_offline_replay(self):
        self.consent(True)
        reporter = self.reporter(traces_sample_rate=1.0)
        reporter.release = 'tiptune@1.2.3'
        with reporter.trace('song.request') as root:
            with reporter.trace('song.extract') as child:
                event_id = reporter.capture_exception('song.extract.failed', ValueError('private message'))
                trace_id, span_id = child.trace_id, child.span_id
        self.post.assert_not_called()
        records = [json.loads(path.read_text())['event'] for path in reporter.pending_dir.glob('*.json')]
        transaction = next(event for event in records if event.get('type') == 'transaction')
        self.assertEqual(transaction['contexts']['trace']['trace_id'], trace_id)
        self.assertEqual(transaction['contexts']['trace']['span_id'], root.span_id)
        self.assertEqual(transaction['contexts']['trace']['status'], 'internal_error')
        self.assertEqual(transaction['spans'][0]['span_id'], span_id)
        self.assertEqual(transaction['spans'][0]['parent_span_id'], root.span_id)
        self.post.side_effect = httpx.ConnectError('offline')
        reporter.process_pending()
        restored = self.reporter()
        restored.release = 'tiptune@9.9.9'
        self.post.reset_mock(side_effect=True)
        with patch('utils.error_reporting.time.time', return_value=time.time() + 4000):
            restored.process_pending()
        uploaded = [json.loads(call.kwargs['content'].split(b'\n', 2)[2]) for call in self.post.call_args_list]
        self.assertEqual({event.get('type', 'event') for event in uploaded}, {'event', 'transaction'})
        error = next(event for event in uploaded if event['event_id'] == event_id)
        self.assertEqual(error['contexts']['trace']['trace_id'], trace_id)
        self.assertEqual(error['contexts']['trace']['span_id'], span_id)
        self.assertTrue(all(event['release'] == 'tiptune@1.2.3' for event in uploaded))
        self.assertNotIn('private message', json.dumps(uploaded))
        self.assertEqual(restored.status()['pending'], 0)

    def test_trace_sanitization_drops_untrusted_attributes_names_and_invalid_ids(self):
        self.consent(True)
        reporter = self.reporter(traces_sample_rate=1.0)
        with reporter.trace('song.request') as span:
            span.set_data('request.body', 'private payload')
            span.set_tag('username', 'private user')
            with reporter.trace('song.extract') as child:
                child.set_data('gen_ai.prompt', 'private prompt')
            with span.start_child(op='http.client', name='https://private.example?token=private'):
                pass
        safe = json.loads(next(reporter.pending_dir.glob('*.json')).read_text())['event']
        self.assertNotIn('private', json.dumps(safe))
        self.assertEqual(len(safe['spans']), 1)
        self.assertEqual(sanitize_report(sanitize_report(safe)), safe)
        safe['contexts']['trace']['trace_id'] = '0' * 32
        self.assertIsNone(sanitize_report(safe))
        self.assertIsNone(sanitize_report({'type': 'transaction', 'transaction': 'private operation'}))
        error = sanitize_report({'contexts': {'trace': {'trace_id': 'private', 'span_id': 'private'}}})
        self.assertNotIn('trace', error['contexts'])

    def test_trace_sampling_can_be_disabled_without_disabling_error_reporting(self):
        self.consent(True)
        reporter = self.reporter(traces_sample_rate=0.0)
        with reporter.trace('song.request') as span:
            self.assertIsNone(span)
            reporter.capture_exception('app.error', ValueError())
        self.assertIsNone(reporter._trace_client)
        reporter.process_pending()
        self.assertEqual(self.post.call_count, 1)
        self.assertEqual(envelope_event(self.post.call_args.kwargs['content'])['tags']['event_type'], 'app.error')

    def test_configured_credentials_cannot_be_uploaded_as_trace_ids(self):
        # Public, deterministic test data; this is not an application credential.
        test_trace_id = '0123456789abcdef0123456789abcdef'
        self.config_path.write_text('[General]\nsend_error_reports=true\n[OBS]\npassword=' + test_trace_id)
        reporter = self.reporter()
        reporter.capture_event({'contexts': {'trace': {'trace_id': test_trace_id, 'span_id': '1234567890abcdef'}}})
        stored = next(reporter.pending_dir.glob('*.json')).read_text()
        self.assertNotIn(test_trace_id, stored)
        reporter.process_pending()
        self.assertNotIn(test_trace_id, self.post.call_args.kwargs['content'].decode())
        self.assertNotIn('trace', envelope_event(self.post.call_args.kwargs['content'])['contexts'])

    def test_short_span_clock_skew_is_preserved_and_unrelated_timestamps_are_rejected(self):
        trace_id = '0123456789abcdef0123456789abcdef'
        start, end = 1_700_000_000.0, 1_700_000_000.001
        child = {'trace_id': trace_id, 'span_id': '1234567890abcdef',
                 'parent_span_id': 'abcdef0123456789', 'description': 'song.extract', 'op': 'function',
                 'start_timestamp': end + 0.000066, 'timestamp': end + 0.00008}
        event = {'type': 'transaction', 'transaction': 'song.request',
                 'start_timestamp': start, 'timestamp': end,
                 'contexts': {'trace': {'trace_id': trace_id, 'span_id': 'abcdef0123456789'}},
                 'spans': [child, {**child, 'timestamp': end + 1}]}
        safe = sanitize_report(event)
        self.assertEqual(len(safe['spans']), 1)
        self.assertEqual(safe['spans'][0]['start_timestamp'], child['start_timestamp'])
        self.assertEqual(safe['spans'][0]['timestamp'], child['timestamp'])
        self.assertEqual(sanitize_report(sanitize_report(safe)), safe)

    def test_revoking_consent_during_operation_prevents_trace_storage_and_delivery(self):
        self.consent(True)
        reporter = self.reporter(traces_sample_rate=1.0)
        with reporter.trace('song.request'):
            reporter.capture_exception('app.error', ValueError())
            self.consent(False)
            reporter.configure()
        reporter.process_pending()
        self.assertEqual(reporter.status()['pending'], 0)
        self.post.assert_not_called()

    def test_traces_reserve_queue_capacity_for_errors_and_are_not_deduplicated(self):
        self.consent(True)
        reporter = self.reporter(traces_sample_rate=1.0)
        for _ in range(MAX_REPORTS):
            with reporter.trace('song.request'):
                pass
        self.assertEqual(reporter.status()['pending'], MAX_REPORTS // 2)
        self.assertIsNotNone(reporter.capture_exception('app.error', ValueError()))
        self.assertEqual(reporter.status()['pending'], MAX_REPORTS // 2 + 1)
        self.post.assert_not_called()

    def test_tracing_setup_failure_does_not_change_operation_result(self):
        self.consent(True)
        reporter = self.reporter(traces_sample_rate=1.0)
        with patch('utils.error_reporting.sentry_sdk.Client', side_effect=RuntimeError('unavailable')):
            with reporter.trace('song.request') as span:
                self.assertIsNone(span)
        self.assertEqual(reporter.status()['pending'], 0)
        self.post.assert_not_called()


class ConcurrentTracingTests(unittest.IsolatedAsyncioTestCase):
    async def test_background_task_starts_a_new_trace_after_its_parent_request_finishes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            config_path = root / 'config.ini'
            config_path.write_text('[General]\nsend_error_reports=true')
            reporter = ErrorReporter(config_path, root / 'cache', DSN, start_worker=False, traces_sample_rate=1.0)
            self.addCleanup(reporter.close)
            ready = asyncio.Event()

            async def background_work():
                await ready.wait()
                with reporter.trace('song.request') as span:
                    reporter.capture_exception('app.background.error', ValueError())
                    return span.trace_id

            with reporter.trace('POST /api/queue/add') as parent:
                task = asyncio.create_task(background_work())
            ready.set()
            trace_id = await asyncio.wait_for(task, 2)
            self.assertNotEqual(parent.trace_id, trace_id)
            records = [json.loads(path.read_text())['event'] for path in reporter.pending_dir.glob('*.json')]
            self.assertEqual(len(records), 3)
            error = next(event for event in records if event.get('type') != 'transaction')
            self.assertEqual(error['contexts']['trace']['trace_id'], trace_id)

    async def test_concurrent_operations_keep_separate_scopes_and_restore_the_callers_scope(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            config_path = root / 'config.ini'
            config_path.write_text('[General]\nsend_error_reports=true')
            reporter = ErrorReporter(config_path, root / 'cache', DSN, start_worker=False, traces_sample_rate=1.0)
            self.addCleanup(reporter.close)
            original_client = sentry_sdk.get_client()
            original_scope = sentry_sdk.get_isolation_scope()
            ready = asyncio.Event()
            started = 0

            async def operation(name, event_type):
                nonlocal started
                with reporter.trace(name) as span:
                    started += 1
                    if started == 2:
                        ready.set()
                    await asyncio.wait_for(ready.wait(), 2)
                    event_id = reporter.capture_exception(event_type, ValueError())
                    await asyncio.sleep(0)
                    return event_id, span.trace_id

            expected = await asyncio.gather(operation('song.request', 'app.first.error'),
                                            operation('POST /api/queue/add', 'app.second.error'))
            records = [json.loads(path.read_text())['event'] for path in reporter.pending_dir.glob('*.json')]
            self.assertEqual(len(records), 4)
            self.assertEqual(len({trace_id for _, trace_id in expected}), 2)
            for event_id, trace_id in expected:
                event = next(record for record in records if record['event_id'] == event_id)
                self.assertEqual(event['contexts']['trace']['trace_id'], trace_id)
            self.assertIs(sentry_sdk.get_isolation_scope(), original_scope)
            self.assertEqual(sentry_sdk.get_client().is_active(), original_client.is_active())
            self.assertIsNone(reporter._active_span.get())


class ErrorReportingApiTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.storage = tempfile.TemporaryDirectory()
        cls.root = Path(cls.storage.name)
        cls.environment = patch.dict('os.environ', {'TIPTUNE_CONFIG': str(cls.root / 'startup.ini'),
            'TIPTUNE_CACHE_DIR': str(cls.root / 'startup-cache'), 'TIPTUNE_SENTRY_DSN': '',
            'TIPTUNE_DEFAULT_LOG_PATH': str(cls.root / 'startup.log')})
        cls.environment.start()
        cls.app = importlib.import_module('app')

    @classmethod
    def tearDownClass(cls):
        cls.environment.stop()
        logging.shutdown()
        cls.storage.cleanup()

    async def asyncSetUp(self):
        self.config_path = self.root / 'config.ini'
        self.config_path.write_text('[General]\nshow_history_debug=true\nsend_error_reports=false\n[OBS]\nenabled=false')
        cfg = configparser.ConfigParser()
        cfg.read(self.config_path)
        self.reporter = ErrorReporter(self.config_path, self.root / 'cache', DSN, start_worker=False)
        self.addCleanup(self.reporter.close)
        for target, value in [('config', cfg), ('config_path', self.config_path), ('error_reporter', self.reporter),
                              ('get_cache_dir', Mock(return_value=self.root / 'cache')), ('_setup_logging', Mock()),
                              ('Actions', Mock(return_value=Mock(chatdj_enabled=False)))]:
            self.enterContext(patch.object(self.app, target, value))
        self.enterContext(patch('helpers.refresh_spotify_client'))
        self.post = self.enterContext(patch('utils.error_reporting.httpx.post', return_value=httpx.Response(200)))
        service = self.app.SongRequestService()
        service._refresh_obs_integration_from_config = AsyncMock()
        self.client = TestClient(TestServer(self.app.WebUI(service)._app))
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)

    async def test_consent_is_separate_from_history_display_and_updates_live(self):
        response = await self.client.get('/api/error-reporting')
        self.assertFalse((await response.json())['enabled'])
        response = await self.client.post('/api/error-reporting/test')
        self.assertEqual(response.status, 409)
        response = await self.client.post('/api/config', json={'General': {'send_error_reports': 'true'}})
        self.assertEqual(await response.json(), {'ok': True})
        response = await self.client.get('/api/config')
        cfg = (await response.json())['config']['General']
        self.assertEqual(cfg['show_history_debug'], 'true')
        self.assertEqual(cfg['send_error_reports'], 'true')
        response = await self.client.post('/api/error-reporting/test')
        test_report = await response.json()
        self.assertTrue(test_report['queued'])
        response = await self.client.post('/api/error-reporting/event', json={'type': 'TypeError', 'message': 'private', 'frames': []})
        self.assertTrue((await response.json())['queued'])
        response = await self.client.post('/api/config', json={'General': {'send_error_reports': 'false'}})
        self.assertEqual(await response.json(), {'ok': True})
        self.assertEqual(self.reporter.status()['pending'], 0)
        self.post.assert_not_called()

    async def test_invalid_consent_cannot_partially_write_other_settings(self):
        before = self.config_path.read_text()
        response = await self.client.post('/api/config', json={'General': {'send_error_reports': 'maybe', 'song_cost': '99'}})
        self.assertEqual(response.status, 400)
        self.assertEqual(before, self.config_path.read_text())

    async def test_test_report_links_to_its_api_transaction_and_excludes_query_data(self):
        await self.client.post('/api/config', json={'General': {'send_error_reports': 'true'}})
        await self.client.get('/api/error-reporting?token=private-query')
        self.assertEqual(self.reporter.status()['pending'], 0)
        response = await self.client.post('/api/error-reporting/test?username=private-query')
        report = await response.json()
        records = [json.loads(path.read_text())['event'] for path in self.reporter.pending_dir.glob('*.json')]
        self.assertEqual(len(records), 2)
        error = next(event for event in records if event['event_id'] == report['report_id'])
        transaction = next(event for event in records if event.get('type') == 'transaction')
        self.assertEqual(transaction['transaction'], 'POST /api/error-reporting/test')
        self.assertEqual(error['contexts']['trace']['trace_id'], transaction['contexts']['trace']['trace_id'])
        self.assertNotIn('private-query', json.dumps(records))
        self.post.assert_not_called()

    async def test_disabled_relay_ignores_events_and_enabled_relay_limits_payload_size(self):
        response = await self.client.post('/api/error-reporting/event', json={'message': 'private'})
        self.assertFalse((await response.json())['queued'])
        await self.client.post('/api/config', json={'General': {'send_error_reports': 'true'}})
        response = await self.client.post('/api/error-reporting/event', data=b'x' * 70000)
        self.assertEqual(response.status, 413)
        response = await self.client.post('/api/error-reporting/event', json=[])
        self.assertEqual(response.status, 400)
        self.post.assert_not_called()


if __name__ == '__main__':
    unittest.main()
