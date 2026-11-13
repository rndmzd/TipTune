import asyncio
import configparser
from contextlib import nullcontext
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
from aiohttp.test_utils import TestClient, TestServer

import app
from utils.error_reporting import ErrorReporter, ErrorReportingHandler
from utils.structured_logging import StructuredLogger


FEED = 'https://events.example/private-feed-token'
CURSOR = FEED + '?cursor=one'


class EventsAPIPollingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.config = configparser.ConfigParser()
        self.config.read_dict({'Events API': {'url': FEED}})
        self.service = app.SongRequestService.__new__(app.SongRequestService)
        self.service._stop_event = asyncio.Event()
        self.service._events_api_wake = asyncio.Event()
        self.service.events_api_status = ''
        self.service._events_subscribers = set()
        self.service._request_history_recent_max = 1000
        self.service.publish_events_api_event = Mock()
        self.service._handle_event = AsyncMock()
        self.reporter = Mock()
        self.reporter.operation.side_effect = nullcontext
        self.reporter.trace.side_effect = lambda *_: nullcontext()
        self.enterContext(patch.object(app, 'config', self.config))
        self.enterContext(patch.object(app, 'logger'))
        self.enterContext(patch.object(app, 'error_reporter', self.reporter))

    async def poll_responses(self, *responses):
        urls, waits = [], []
        now = 0.0
        remaining = iter(responses)

        def send(request):
            urls.append(str(request.url))
            response = next(remaining)
            return response(request) if callable(response) else response

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
        self.assertEqual(waits, [1, 3, 6, 10, 10, 5, 10, 1, 3])
        self.assertEqual(urls, [FEED] + [CURSOR] * 7 + [next_cursor])
        self.service.publish_events_api_event.assert_called_once_with(event)
        self.service._handle_event.assert_awaited_once_with(event)
        details = [call.kwargs['details'] for call in self.reporter.capture_exception.call_args_list]
        self.assertIn({'stage': 'continuation_poll', 'status_code': 503}, details)

    async def test_restarting_after_a_rejected_cursor_skips_replayed_tips_and_accepts_new_ones(self):
        tip = {'method': 'tip', 'id': '104-0', 'object': {'tip': {'tokens': 10}}}
        new_tip = {**tip, 'id': '105-0'}
        next_cursor = FEED + '?cursor=two'
        urls, waits = await self.poll_responses(
            httpx.Response(200, json={'events': [tip], 'nextUrl': CURSOR}),
            httpx.Response(400, json={'error': 'private response'}),
            httpx.Response(200, json={'events': [tip, new_tip], 'nextUrl': next_cursor}),
            httpx.Response(200, json={'events': []}),
        )
        self.assertEqual(urls, [FEED, CURSOR, FEED, next_cursor])
        self.assertEqual(waits, [1, 1, 1, 1])
        self.assertEqual([call.args[0] for call in self.service._handle_event.await_args_list], [tip, new_tip])
        self.assertEqual([call.args[0] for call in self.service.publish_events_api_event.call_args_list], [tip, new_tip])
        self.assertEqual(self.service.events_api_status, 'Events API connected.')
        self.reporter.capture_exception.assert_called_once()
        self.assertEqual(self.reporter.capture_exception.call_args.kwargs['details'],
                         {'stage': 'continuation_poll', 'status_code': 400})

    async def test_repeated_cursor_rejection_backs_off_even_if_the_base_feed_works(self):
        urls, waits = await self.poll_responses(
            httpx.Response(200, json={'events': [], 'nextUrl': CURSOR}),
            httpx.Response(400),
            httpx.Response(200, json={'events': [], 'nextUrl': CURSOR}),
            httpx.Response(410),
            httpx.Response(200, json={'events': [], 'nextUrl': CURSOR}),
            httpx.Response(410),
            httpx.Response(200, json={'events': [], 'nextUrl': CURSOR}),
            httpx.Response(200, json={'events': []}),
        )
        self.assertEqual(urls, [FEED, CURSOR] * 4)
        self.assertEqual(waits, [1, 1, 1, 5, 1, 15, 1, 1])
        self.assertEqual([call.kwargs['details']['status_code']
                          for call in self.reporter.capture_exception.call_args_list], [400, 410])
        self.assertEqual(self.service.events_api_status, 'Events API connected.')

    async def test_rejected_base_feed_backs_off_without_flooding_reports(self):
        urls, waits = await self.poll_responses(
            httpx.Response(400), httpx.Response(400), httpx.Response(400), httpx.Response(400),
        )
        self.assertEqual(urls, [FEED] * 4)
        self.assertEqual(waits, [30, 60, 300, 300])
        self.reporter.capture_exception.assert_called_once()
        self.assertEqual(self.reporter.capture_exception.call_args.kwargs['details'],
                         {'stage': 'feed_poll', 'status_code': 400})
        self.assertIn('Check the URL and access token', self.service.events_api_status)
        self.assertNotIn(FEED, self.service.events_api_status)

    async def test_example_feed_url_does_not_poll_or_report_an_error(self):
        self.config.read('config.ini.example', encoding='utf-8')

        async def stop_on_wait(wait, *, timeout):
            wait.close()
            self.service._stop_event.set()

        send = Mock(side_effect=AssertionError('Unexpected Events API request'))
        client = httpx.AsyncClient(transport=httpx.MockTransport(send))
        with (
            patch.object(app.httpx, 'AsyncClient', return_value=client),
            patch.object(app.asyncio, 'wait_for', new=stop_on_wait),
        ):
            await self.service._events_loop()

        send.assert_not_called()
        self.reporter.capture_exception.assert_not_called()
        self.assertIn('No Events API URL saved', self.service.events_api_status)

    async def test_connection_error_retries_the_same_cursor_and_empty_success_resets_backoff(self):
        def unreachable(request):
            raise httpx.ConnectError('private network error', request=request)

        urls, waits = await self.poll_responses(
            httpx.Response(200, json={'events': [], 'nextUrl': CURSOR}),
            unreachable,
            httpx.Response(200, json={'events': [], 'nextUrl': None}),
            unreachable,
        )
        self.assertEqual(urls, [FEED, CURSOR, CURSOR, CURSOR])
        self.assertEqual(waits, [1, 5, 1, 5])
        self.assertEqual(self.reporter.capture_exception.call_count, 2)

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
            {'stage': 'feed_poll', 'status_code': 429, 'local_attempts_last_minute': 1, 'retry_after_seconds': 40},
            {'stage': 'feed_poll', 'status_code': 429, 'local_attempts_last_minute': 2, 'retry_after_seconds': 20},
            {'stage': 'feed_poll', 'status_code': 429, 'local_attempts_last_minute': 2, 'retry_after_seconds': 0},
            {'stage': 'feed_poll', 'status_code': 429, 'local_attempts_last_minute': 3},
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
                self.service._events_api_wake.set()
                await asyncio.wait_for(task, timeout=2)
            finally:
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

    async def test_saving_a_replacement_url_interrupts_backoff_and_publishes_recovery(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        config_path = root / 'config.ini'
        with config_path.open('w', encoding='utf-8') as f:
            self.config.write(f)
        urls = []
        rejected = asyncio.Event()
        replacement = 'https://events.example/replacement/%62'

        def send(request):
            urls.append(str(request.url))
            if str(request.url) == FEED:
                rejected.set()
                return httpx.Response(400)
            return httpx.Response(200, json={'events': []})

        client = httpx.AsyncClient(transport=httpx.MockTransport(send))
        with (
            patch.object(app.httpx, 'AsyncClient', return_value=client),
            patch.object(app, 'config_path', config_path),
            patch.object(app, '_setup_logging'),
            patch('helpers.refresh_spotify_client'),
        ):
            task = asyncio.create_task(self.service._events_loop())
            try:
                await asyncio.wait_for(rejected.wait(), timeout=2)
                async with TestClient(TestServer(app.WebUI(self.service)._app)) as ui:
                    response = await ui.get('/api/events/sse')
                    data = await asyncio.wait_for(response.content.readuntil(b'\n\n'), 2)
                    self.assertIn(b'event: feed-status\n', data)
                    self.assertIn(b'HTTP 400', data)
                    self.assertNotIn(FEED.encode(), data)

                    saved = await ui.post('/api/config', json={'Events API': {'url': replacement}})
                    self.assertEqual(saved.status, 200)
                    self.assertTrue((await saved.json())['ok'])
                    masked = await ui.get('/api/config')
                    self.assertEqual((await masked.json())['config']['Events API']['url'], '')
                    self.assertEqual(await asyncio.wait_for(response.content.readuntil(b'\n\n'), 2),
                                     b'event: feed-status\ndata: "Events API connected."\n\n')
                    response.close()
                self.assertEqual(urls[:2], [FEED, replacement])
            finally:
                self.service._stop_event.set()
                self.service._events_api_wake.set()
                await asyncio.wait_for(task, timeout=2)

    async def test_feed_error_reports_and_logs_keep_urls_and_response_bodies_private(self):
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
                httpx.Response(400, json={'reason': 'private-rejection'}),
                httpx.Response(400, json={'reason': 'private-rejection'}),
                httpx.Response(429, headers={'Retry-After': '15', 'X-Private': 'private-header'},
                               json={'events': ['private-event']}),
            )

        # Both 400s have the same traceback. Keep the different failed stages.
        self.assertEqual(capture.call_count, 3)
        stored = [p.read_text(encoding='utf-8') for p in reporter.pending_dir.glob('*.json')]
        events = [json.loads(item)['event'] for item in stored]
        expected = [
            {'stage': 'continuation_poll', 'status_code': 400},
            {'stage': 'feed_poll', 'status_code': 400},
            {'stage': 'feed_poll', 'status_code': 429, 'local_attempts_last_minute': 4, 'retry_after_seconds': 15},
        ]
        self.assertTrue(all(event['tags']['event_type'] == 'events_api.poll.error' for event in events))
        self.assertCountEqual([event['contexts']['failure'] for event in events], expected)
        with patch('utils.error_reporting.httpx.post', return_value=httpx.Response(200)) as post:
            reporter.process_pending()
        self.assertEqual(post.call_count, 3)
        uploaded = [call.kwargs['content'].decode('utf-8') for call in post.call_args_list]
        self.assertCountEqual([json.loads(item.split('\n', 2)[2])['contexts']['failure']
                               for item in uploaded], expected)
        httpx_logger.info('unrelated HTTP request')
        self.assertIn('unrelated HTTP request', logs.getvalue())
        for private in (FEED, 'Retry-After', 'X-Private', 'private-header', 'private-event', 'private-rejection'):
            for output in (*stored, *uploaded, logs.getvalue()):
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
