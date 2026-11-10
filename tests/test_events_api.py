import asyncio
import configparser
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
import io
import json
import logging
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import httpx

import app
from utils.error_reporting import ErrorReporter, ErrorReportingHandler
from utils.structured_logging import StructuredLogger


FEED = 'https://events.example/private-feed-token'
CURSOR = FEED + '?cursor=one'


class EventsAPIPollingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.config = configparser.ConfigParser()
        self.config.read_dict({'Events API': {'url': FEED}})
        self.service = SimpleNamespace(
            _stop_event=asyncio.Event(), publish_events_api_event=Mock(), _handle_event=AsyncMock(),
        )
        self.reporter = Mock()
        self.enterContext(patch.object(app, 'config', self.config))
        self.enterContext(patch.object(app, 'logger'))
        self.enterContext(patch.object(app, 'error_reporter', self.reporter))

    async def poll_responses(self, *responses):
        urls, waits = [], []
        now = 0.0
        remaining = iter(responses)

        def send(request):
            urls.append(str(request.url))
            return next(remaining)

        async def advance_time(wait, *, timeout):
            nonlocal now
            wait.close()
            waits.append(timeout)
            now += timeout
            if len(waits) == len(responses):
                self.service._stop_event.set()
                return
            raise asyncio.TimeoutError

        client = httpx.AsyncClient(transport=httpx.MockTransport(send))
        with (
            patch.object(app.httpx, 'AsyncClient', return_value=client),
            patch.object(app.asyncio, 'wait_for', new=advance_time),
            patch.object(app, 'time', SimpleNamespace(monotonic=lambda: now)),
        ):
            await app.SongRequestService._events_loop(self.service)
        return urls, waits

    async def test_backoff_plateaus_and_an_empty_success_resets_it_and_advances_the_cursor(self):
        event = {'method': 'tip', 'object': {'tip': {'tokens': 10}}}
        next_cursor = FEED + '?cursor=two'
        urls, waits = await self.poll_responses(
            httpx.Response(200, json={'events': [event], 'nextUrl': CURSOR}),
            httpx.Response(429, json={'nextUrl': 'https://events.example/ignore-this-cursor'}),
            httpx.Response(429, headers={'Retry-After': '2'}),
            httpx.Response(429),
            httpx.Response(429),
            httpx.Response(503),
            httpx.Response(429),
            httpx.Response(200, json={'events': [], 'nextUrl': next_cursor}),
            httpx.Response(429),
        )
        self.assertEqual(waits, [1, 3, 6, 10, 10, 1, 10, 1, 3])
        self.assertEqual(urls, [FEED] + [CURSOR] * 7 + [next_cursor])
        self.service.publish_events_api_event.assert_called_once_with(event)
        self.service._handle_event.assert_awaited_once_with(event)
        app.logger.exception.assert_called_once()
        self.assertEqual(app.logger.exception.call_args.kwargs['exc'].response.status_code, 503)

    async def test_retry_after_seconds_and_date_and_rolling_local_attempt_count(self):
        now = datetime(2026, 11, 10, tzinfo=timezone.utc)
        with patch.object(app, 'datetime') as clock:
            clock.now.return_value = now
            _, waits = await self.poll_responses(
                httpx.Response(429, headers={'Retry-After': '40'}),
                httpx.Response(429, headers={'Retry-After': format_datetime(now + timedelta(seconds=20), usegmt=True)}),
                httpx.Response(429, headers={'Retry-After': format_datetime(now - timedelta(seconds=10), usegmt=True)}),
                httpx.Response(429, headers={'Retry-After': '-1'}),
            )
        self.assertEqual(waits, [40, 20, 10, 10])
        details = [call.kwargs['details'] for call in self.reporter.capture_exception.call_args_list]
        self.assertEqual(details, [
            {'status_code': 429, 'local_attempts_last_minute': 1, 'retry_after_seconds': 40},
            {'status_code': 429, 'local_attempts_last_minute': 2, 'retry_after_seconds': 20},
            {'status_code': 429, 'local_attempts_last_minute': 2, 'retry_after_seconds': 0},
            {'status_code': 429, 'local_attempts_last_minute': 3},
        ])

    async def test_configured_polling_rate_sets_the_normal_gap_and_backoff_floor(self):
        for rate, normal_gap, limited_gap in (('', 1, 3), ('1000', 0.6, 3), ('120', 5, 5)):
            with self.subTest(rate=rate):
                self.config.set('Events API', 'max_requests_per_minute', rate)
                self.service._stop_event.clear()
                _, waits = await self.poll_responses(
                    httpx.Response(200, json={'events': []}),
                    httpx.Response(429, headers={'Retry-After': '1'}),
                )
                self.assertEqual(waits, [normal_gap, limited_gap])

    async def test_changing_only_the_polling_rate_preserves_cursor_and_backoff(self):
        def save_rate(*args, **kwargs):
            self.config.set('Events API', 'max_requests_per_minute', '1000')

        app.logger.warning.side_effect = save_rate
        urls, waits = await self.poll_responses(
            httpx.Response(200, json={'events': [], 'nextUrl': CURSOR}),
            httpx.Response(429),
            httpx.Response(429),
            httpx.Response(200, json={'events': []}),
        )
        self.assertEqual(urls, [FEED, CURSOR, CURSOR, CURSOR])
        self.assertEqual(waits, [1, 3, 6, 0.6])

    async def test_shutdown_interrupts_a_long_retry_after(self):
        responded = asyncio.Event()

        def send(request):
            responded.set()
            return httpx.Response(429, headers={'Retry-After': '120'})

        client = httpx.AsyncClient(transport=httpx.MockTransport(send))
        with patch.object(app.httpx, 'AsyncClient', return_value=client):
            task = asyncio.create_task(app.SongRequestService._events_loop(self.service))
            try:
                await asyncio.wait_for(responded.wait(), timeout=2)
                self.assertFalse(task.done())
                self.service._stop_event.set()
                await asyncio.wait_for(task, timeout=2)
            finally:
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

    async def test_rate_limit_report_and_logs_keep_the_feed_private(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        config_path = root / 'config.ini'
        config_path.write_text('[General]\nsend_error_reports = true\n', encoding='utf-8')
        reporter = ErrorReporter(config_path, root / 'cache', 'https://public@example.org/1', start_worker=False)
        self.addCleanup(reporter.close)
        logs = io.StringIO()
        logger = logging.Logger('isolated', level=logging.INFO)
        logger.addHandler(logging.StreamHandler(logs))
        logger.addHandler(ErrorReportingHandler(reporter))
        httpx_logger = logging.getLogger('httpx')
        request_logs = logging.StreamHandler(logs)
        request_logs.addFilter(app._exclude_events_api_http_logs)
        httpx_logger.addHandler(request_logs)
        self.addCleanup(httpx_logger.removeHandler, request_logs)
        with (
            patch.object(app, 'logger', StructuredLogger(logger)),
            patch.object(app, 'error_reporter', reporter),
            patch.object(reporter, 'capture_exception', wraps=reporter.capture_exception) as capture,
        ):
            await self.poll_responses(
                httpx.Response(200, json={'events': [], 'nextUrl': CURSOR}),
                httpx.Response(429, headers={'Retry-After': '15', 'X-Private': 'private-header'},
                               json={'events': ['private-event']}),
            )

        capture.assert_called_once()
        report_path, = reporter.pending_dir.glob('*.json')
        stored = report_path.read_text(encoding='utf-8')
        event = json.loads(stored)['event']
        self.assertEqual(event['tags']['event_type'], 'events_api.poll.error')
        expected = {
            'status_code': 429, 'local_attempts_last_minute': 2, 'retry_after_seconds': 15,
        }
        self.assertEqual(event['contexts']['failure'], expected)
        with patch('utils.error_reporting.httpx.post', return_value=httpx.Response(200)) as post:
            reporter.process_pending()
        post.assert_called_once()
        uploaded = post.call_args.kwargs['content'].decode('utf-8')
        self.assertEqual(json.loads(uploaded.split('\n', 2)[2])['contexts']['failure'], expected)
        httpx_logger.info('unrelated HTTP request')
        self.assertIn('unrelated HTTP request', logs.getvalue())
        for private in (FEED, 'Retry-After', 'X-Private', 'private-header', 'private-event'):
            for output in (stored, uploaded, logs.getvalue()):
                self.assertNotIn(private, output)

    def test_new_config_and_poll_client_share_the_one_second_default(self):
        path = Path(self.enterContext(tempfile.TemporaryDirectory())) / 'config.ini'
        app._update_ini_file(path, {'Events API': {'url': FEED}})
        config = configparser.ConfigParser()
        config.read(path, encoding='utf-8')
        rate = config.getint('Events API', 'max_requests_per_minute')
        self.assertEqual(rate, 600)
        self.assertEqual(app.EventsAPIClient(FEED, rate).poll_interval_seconds, 1)
        self.assertEqual(app.EventsAPIClient(FEED).poll_interval_seconds, 1)
