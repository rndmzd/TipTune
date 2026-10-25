"""Opt-in, bounded error reports. Application data never enters the upload queue."""

import configparser
from collections import deque
from contextlib import contextmanager, ExitStack
from contextvars import ContextVar
from datetime import datetime
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import platform
import re
import sys
import threading
import time
import traceback
from urllib.parse import urlsplit
import uuid

import httpx
import sentry_sdk
from sentry_sdk.transport import Transport

from utils.runtime_paths import get_resource_path
from utils.song_extraction_debug import sanitize_extraction_debug
from utils.support_diagnostics import (
    FAILURE_CODES, SUPPORT_OPERATION, SupportOperation, environment_for_report,
    new_support_id, sanitize_environment, valid_support_id,
)


MAX_REPORTS = 100
MAX_REPORT_BYTES = 64 * 1024
MAX_AGE_SECONDS = 7 * 24 * 60 * 60
_REPORT_NAME = re.compile(r'^[a-f0-9]{32}\.(?:json|tmp)$')
_ATOM = re.compile(r'^[A-Za-z0-9_.:-]{1,128}$')
_EVENT_TYPES = re.compile(r'^[a-z][a-z0-9_.]{0,127}$')
_ASSET_PATH = re.compile(r'^app:///assets/[A-Za-z0-9_.-]{1,128}\.js$')
_DEBUG_ID = re.compile(r'^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$')
_TRACE_ID = re.compile(r'^[a-f0-9]{32}$')
_SPAN_ID = re.compile(r'^[a-f0-9]{16}$')
# Fixed names only: URLs, query strings, and request/provider data never name spans.
TRACE_OPERATIONS = {
    **{f'POST /api/{path}': 'http.server' for path in (
        'config', 'queue/add', 'queue/pause', 'queue/resume', 'queue/seek', 'queue/move',
        'queue/delete', 'queue/next', 'playback/pause', 'playback/resume', 'spotify/device',
        'spotify/auth/start', 'obs/scenes', 'obs/ensure_sources', 'obs/ensure_browser_source',
        'obs/ensure_spotify_audio_capture', 'obs/ensure_tiptune_audio_capture',
        'obs/now_playing', 'obs/test_overlay', 'history/clear',
        'error-reporting/event', 'error-reporting/test',
    )},
    'GET /api/spotify/search': 'http.server',
    'GET /api/music/search': 'http.server',
    'song.request': 'task', 'song.extract': 'function',
    'song.lookup.spotify': 'function', 'song.lookup.youtube': 'function',
    'song.queue': 'queue.submit', 'song.playback': 'function',
}
_TRACE_STATUSES = frozenset(('ok', 'internal_error', 'cancelled', 'unknown_error',
                            'invalid_argument', 'not_found', 'permission_denied',
                            'unauthenticated', 'resource_exhausted', 'unavailable',
                            'deadline_exceeded'))
_transport_active = threading.local()


def reporting_enabled(config_path):
    """Read durable consent immediately before collecting or sending a report."""
    try:
        cfg = configparser.ConfigParser(interpolation=None)
        cfg.read(config_path, encoding='utf-8')
        return cfg.getboolean('General', 'send_error_reports', fallback=False)
    except (OSError, ValueError, configparser.Error):
        return False


def load_destination():
    try:
        settings = json.loads(get_resource_path('sentry.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        settings = {}
    if not isinstance(settings, dict):
        settings = {}
    dsn = os.getenv('TIPTUNE_SENTRY_DSN', settings.get('dsn', ''))
    return (dsn.strip() if isinstance(dsn, str) else '',
            os.getenv('TIPTUNE_SENTRY_ENVIRONMENT', settings.get('environment', 'production')))


def load_trace_sample_rate():
    try:
        settings = json.loads(get_resource_path('sentry.json').read_text(encoding='utf-8'))
        value = settings.get('traces_sample_rate', 1.0) if isinstance(settings, dict) else 0
        rate = float(os.getenv('TIPTUNE_SENTRY_TRACES_SAMPLE_RATE', value))
        return rate if math.isfinite(rate) and 0 <= rate <= 1 else 0.0
    except (OSError, ValueError, TypeError):
        return 0.0


def app_version():
    value = os.getenv('TIPTUNE_APP_VERSION', '')
    if not value:
        try:
            value = json.loads(get_resource_path('package.json').read_text(encoding='utf-8'))['version']
        except (OSError, ValueError, KeyError):
            value = 'unknown'
    return value if isinstance(value, str) and re.fullmatch(r'[0-9A-Za-z.+-]{1,64}', value) else 'unknown'


def _atom(value, fallback='unknown'):
    return value if isinstance(value, str) and _ATOM.fullmatch(value) else fallback


def _frames(frames, javascript=False):
    """Keep code locations and canonical bundle references, never machine paths."""
    output = []
    for frame in (frames if isinstance(frames, list) else [])[-40:]:
        if not isinstance(frame, dict):
            continue
        filename = str(frame.get('filename', '')).replace('\\', '/').rsplit('/', 1)[-1]
        if not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', filename):
            continue
        item = {'filename': filename}
        if not javascript:
            item['function'] = _atom(frame.get('function'), '<anonymous>')
        asset_path = frame.get('abs_path')
        if javascript and isinstance(asset_path, str) and _ASSET_PATH.fullmatch(asset_path) and asset_path.rsplit('/', 1)[-1] == filename:
            item['abs_path'] = asset_path
            debug_id = frame.get('debug_id')
            if isinstance(debug_id, str) and _DEBUG_ID.fullmatch(debug_id):
                item['debug_id'] = debug_id
        for key in ('lineno', 'colno'):
            value = frame.get(key)
            if type(value) is int and 0 < value < 10_000_000:
                item[key] = value
        output.append(item)
    return output


def _trace_context(context):
    if not isinstance(context, dict):
        return None
    trace_id, span_id = context.get('trace_id'), context.get('span_id')
    if (not isinstance(trace_id, str) or not _TRACE_ID.fullmatch(trace_id) or int(trace_id, 16) == 0
            or not isinstance(span_id, str) or not _SPAN_ID.fullmatch(span_id) or int(span_id, 16) == 0):
        return None
    result = {'trace_id': trace_id, 'span_id': span_id}
    parent = context.get('parent_span_id')
    if isinstance(parent, str) and _SPAN_ID.fullmatch(parent) and int(parent, 16):
        result['parent_span_id'] = parent
    if context.get('op') in TRACE_OPERATIONS.values():
        result['op'] = context['op']
    if isinstance(context.get('status'), str) and context['status'] in _TRACE_STATUSES:
        result['status'] = context['status']
    return result


def _trace_timestamp(value):
    if isinstance(value, datetime):
        value = value.timestamp()
    elif isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
        except (ValueError, OverflowError, OSError):
            return None
    return value if type(value) in (int, float) and math.isfinite(value) and value > 0 else None


def sanitize_transaction(event):
    """Reconstruct sampled transactions; no SDK attributes or free text survive."""
    name = event.get('transaction')
    if not isinstance(name, str) or name not in TRACE_OPERATIONS:
        return None
    contexts = event.get('contexts')
    trace = _trace_context(contexts.get('trace')) if isinstance(contexts, dict) else None
    start, end = _trace_timestamp(event.get('start_timestamp')), _trace_timestamp(event.get('timestamp'))
    if not trace or start is None or end is None or not 0 <= end - start <= 86_400:
        return None
    trace['op'] = TRACE_OPERATIONS[name]
    result = {
        'type': 'transaction',
        'event_id': event.get('event_id') if _TRACE_ID.fullmatch(str(event.get('event_id', ''))) else uuid.uuid4().hex,
        'transaction': name, 'transaction_info': {'source': 'custom'},
        'platform': 'python', 'start_timestamp': start, 'timestamp': end,
        'contexts': {'trace': trace}, 'tags': {'component': 'python'}, 'spans': [],
    }
    tags = event.get('tags')
    if isinstance(tags, dict) and valid_support_id(tags.get('support_id')):
        result['tags']['support_id'] = tags['support_id']
    diagnostics = sanitize_environment(contexts.get('diagnostics'))
    if diagnostics:
        result['contexts']['diagnostics'] = diagnostics
    spans = event.get('spans')
    for span in (spans if isinstance(spans, list) else [])[:50]:
        if (not isinstance(span, dict) or not isinstance(span.get('description'), str)
                or span['description'] not in TRACE_OPERATIONS):
            continue
        context = _trace_context(span)
        span_start, span_end = _trace_timestamp(span.get('start_timestamp')), _trace_timestamp(span.get('timestamp'))
        if (not context or context['trace_id'] != trace['trace_id'] or span_start is None or span_end is None
                or not start <= span_start <= span_end <= end):
            continue
        context.update({'op': TRACE_OPERATIONS[span['description']], 'description': span['description'],
                        'start_timestamp': span_start, 'timestamp': span_end})
        result['spans'].append(context)
    release = event.get('release')
    if isinstance(release, str) and re.fullmatch(r'tiptune@[0-9A-Za-z.+-]{1,64}', release):
        result['release'] = release
    if _atom(event.get('environment'), ''):
        result['environment'] = event['environment']
    return result


def sanitize_report(event):
    """Allowlist the complete event, including events restored from disk or the UI.

    Free-form log/exception messages are deliberately replaced: they can contain
    usernames, request text, authenticated URLs, or provider response bodies.
    """
    if event.get('type') == 'transaction':
        return sanitize_transaction(event)
    tags = event.get('tags') if isinstance(event.get('tags'), dict) else {}
    event_type = tags.get('event_type', 'app.error')
    if not isinstance(event_type, str) or not _EVENT_TYPES.fullmatch(event_type):
        event_type = 'app.error'
    component = tags.get('component')
    if component not in ('python', 'webui', 'overlay', 'desktop'):
        component = 'python'
    result = {
        'event_id': event.get('event_id') if re.fullmatch(r'[a-f0-9]{32}', str(event.get('event_id', ''))) else uuid.uuid4().hex,
        'timestamp': event.get('timestamp') if type(event.get('timestamp')) in (int, float) and math.isfinite(event['timestamp']) else time.time(),
        'level': 'error', 'platform': 'javascript' if component in ('webui', 'overlay') else 'native' if component == 'desktop' else 'python',
        'logger': 'tiptune.errors',
        'tags': {'component': component, 'event_type': event_type},
        'fingerprint': ['{{ default }}', component, event_type],
    }
    if valid_support_id(tags.get('support_id')):
        result['tags']['support_id'] = tags['support_id']
    if event_type in FAILURE_CODES:
        result['tags']['error_code'] = event_type
    values = (event.get('exception') or {}).get('values', [])
    exceptions = []
    for value in (values if isinstance(values, list) else [])[-8:]:
        if not isinstance(value, dict):
            continue
        exception = {'type': _atom(value.get('type'), 'ApplicationError'), 'value': event_type,
                     'mechanism': {'type': 'generic', 'handled': value.get('mechanism', {}).get('handled') is not False}}
        frames = _frames((value.get('stacktrace') or {}).get('frames', []), component in ('webui', 'overlay'))
        if frames:
            exception['stacktrace'] = {'frames': frames}
        exceptions.append(exception)
    result['exception'] = {'values': exceptions or [{'type': 'ApplicationError', 'value': event_type}]}
    # Build metadata only for canonical assets that actually occur in the stack.
    # Existing images are retained on queue replay and repeated SDK sanitization.
    debug_meta = event.get('debug_meta')
    candidates = debug_meta.get('images', []) if isinstance(debug_meta, dict) else []
    known_ids = {}
    for image in (candidates if isinstance(candidates, list) else [])[:40]:
        if (isinstance(image, dict) and image.get('type') == 'sourcemap'
                and isinstance(image.get('code_file'), str) and _ASSET_PATH.fullmatch(image['code_file'])
                and isinstance(image.get('debug_id'), str) and _DEBUG_ID.fullmatch(image['debug_id'])):
            known_ids[image['code_file']] = image['debug_id']
    images = {}
    for exception in result['exception']['values']:
        for frame in exception.get('stacktrace', {}).get('frames', []):
            debug_id = frame.pop('debug_id', None) or known_ids.get(frame.get('abs_path'))
            if frame.get('abs_path') and debug_id:
                images[frame['abs_path']] = {'type': 'sourcemap', 'code_file': frame['abs_path'], 'debug_id': debug_id}
    if images:
        result['debug_meta'] = {'images': list(images.values())}
    details = (event.get('contexts') or {}).get('failure', {})
    failure = {}
    if isinstance(details, dict):
        for key in ('stage', 'request_id'):
            if _atom(details.get(key), ''):
                failure[key] = details[key]
        if details.get('source') in ('spotify', 'youtube'):
            failure['source'] = details['source']
        if type(details.get('status_code')) is int and 100 <= details['status_code'] <= 599:
            failure['status_code'] = details['status_code']
        if type(details.get('exit_code')) is int:
            failure['exit_code'] = details['exit_code']
        if type(details.get('elapsed_ms')) in (int, float) and 0 <= details['elapsed_ms'] <= 86_400_000:
            failure['elapsed_ms'] = details['elapsed_ms']
    result['contexts'] = {'failure': failure, 'os': {'name': platform.system(), 'version': platform.release()},
                          'runtime': {'name': 'CPython', 'version': platform.python_version()} if component == 'python'
                          else {'name': 'Web UI' if component in ('webui', 'overlay') else 'Tauri'}}
    trace = _trace_context((event.get('contexts') or {}).get('trace'))
    if trace:
        result['contexts']['trace'] = trace
    diagnostics = sanitize_environment((event.get('contexts') or {}).get('diagnostics'))
    if diagnostics:
        result['contexts']['diagnostics'] = diagnostics
    crumbs = (event.get('breadcrumbs') or {}).get('values', [])
    result['breadcrumbs'] = {'values': [
        {'category': 'application', 'message': crumb['message'], 'level': 'info'}
        for crumb in (crumbs if isinstance(crumbs, list) else [])[-50:]
        if isinstance(crumb, dict) and isinstance(crumb.get('message'), str) and _EVENT_TYPES.fullmatch(crumb['message'])
    ]}
    release = event.get('release')
    if isinstance(release, str) and re.fullmatch(r'tiptune@[0-9A-Za-z.+-]{1,64}', release):
        result['release'] = release
    if _atom(event.get('environment'), ''):
        result['environment'] = event['environment']
    return result


class ConsentTransport(Transport):
    """Synchronous SDK transport, used exclusively by our background retry worker."""

    def __init__(self, options, reporter):
        super().__init__(options)
        self.reporter = reporter
        self.delivered = False
        self.discard = False
        self.retry_after = 0

    def capture_envelope(self, envelope):
        self.delivered = False
        self.discard = False
        self.retry_after = 0
        if not self.reporter.can_send() or not self.parsed_dsn:
            return
        # Only separately sanitized errors and transactions enter this sender.
        if len(envelope.items) != 1 or envelope.items[0].type not in ('event', 'transaction'):
            return
        auth = self.parsed_dsn.to_auth(client='tiptune.error-reporting')
        response = httpx.post(auth.get_api_url(), content=envelope.serialize(),
                              headers={'X-Sentry-Auth': auth.to_header(), 'Content-Type': 'application/x-sentry-envelope'},
                              timeout=5, follow_redirects=False)
        self.delivered = 200 <= response.status_code < 300
        try:
            self.retry_after = min(3600, max(0, int(response.headers.get('Retry-After', '0'))))
        except ValueError:
            pass
        # Invalid keys/payloads must not create a permanent retry storm.
        if 400 <= response.status_code < 500 and response.status_code not in (408, 429):
            self.reporter.delivery_error = 'The reporting service rejected a report.'
            self.discard = True
        if response.status_code == 429:
            self.retry_after = max(60, self.retry_after)


class TraceQueueTransport(Transport):
    """Tracing never performs network I/O on an application thread."""

    def __init__(self, options, reporter):
        super().__init__(options)
        self.reporter = reporter

    def capture_envelope(self, envelope):
        if len(envelope.items) == 1 and envelope.items[0].type == 'transaction':
            event = envelope.get_transaction_event()
            if event:
                self.reporter.capture_event(event)


class _ActiveSpan:
    def __init__(self, span, parent=None):
        self.span = span
        self.root = parent.root if parent is not None else self
        self.active = True


class ErrorReporter:
    def __init__(self, config_path, cache_dir, dsn=None, environment=None, *, start_worker=True,
                 traces_sample_rate=None):
        self.config_path = Path(config_path)
        self.pending_dir = Path(cache_dir) / 'error-reports' / 'pending'
        destination, default_environment = load_destination()
        self.dsn = destination if dsn is None else dsn
        self.environment = _atom(environment or default_environment, 'production')
        self.release = 'tiptune@' + app_version()
        self.traces_sample_rate = load_trace_sample_rate() if traces_sample_rate is None else traces_sample_rate
        try:
            parsed = urlsplit(self.dsn)
            # A normal Sentry DSN has just /<project>, self-hosted DSNs may have a prefix.
            self.configured = bool(parsed.scheme == 'https' and parsed.hostname and parsed.username
                                   and parsed.password is None and re.fullmatch(r'(?:/[A-Za-z0-9_.-]+)*/\d+', parsed.path))
        except ValueError:
            self.configured = False
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread = None
        self._client = None
        self._transport = None
        self._trace_client = None
        self._active_span = ContextVar('tiptune_reporting_span', default=None)
        self._session_id = None
        self._start_worker = start_worker
        self._enabled = False
        self._crumbs = deque(maxlen=50)
        self._seen = {}
        self.delivery_error = None
        self.last_sent_at = None
        self._delivery_retry_at = 0
        self.configure()

    def can_send(self):
        return self._enabled and self.configured and not self._stop.is_set() and reporting_enabled(self.config_path)

    def _paths(self):
        try:
            return [path for path in self.pending_dir.iterdir() if path.is_file() and _REPORT_NAME.fullmatch(path.name)]
        except OSError:
            return []

    def _purge(self):
        for path in self._paths():
            try:
                path.unlink(missing_ok=True)
            except OSError:
                self.delivery_error = 'Some unsent reports could not be removed. Uploads are stopped.'

    def configure(self):
        with self._lock:
            was_enabled = self._enabled
            self._enabled = reporting_enabled(self.config_path)
            if not self._enabled:
                self._session_id = None
                self._crumbs.clear()
                self._seen.clear()
                self.delivery_error = None
                self._purge()
                self._delivery_retry_at = 0
            else:
                if not was_enabled:
                    self._session_id = uuid.uuid4().hex
                if self.configured and self._start_worker and self._thread is None:
                    self._thread = threading.Thread(target=self._run, name='TipTune error reports', daemon=True)
                    self._thread.start()
            self._wake.set()

    def status(self):
        with self._lock:
            return {'enabled': reporting_enabled(self.config_path), 'configured': self.configured,
                    'pending': len([p for p in self._paths() if p.suffix == '.json']),
                    'last_sent_at': self.last_sent_at, 'error': self.delivery_error}

    def _current_operation(self):
        current = SUPPORT_OPERATION.get()
        return current if current is not None and current.active and current.owner is self else None

    @contextmanager
    def operation(self, support_id=None):
        """A local correlation ID exists even when automatic reporting is off."""
        current = self._current_operation()
        support_id = valid_support_id(support_id)
        if current is not None and (support_id is None or support_id == current.support_id):
            yield current
            return
        current = SupportOperation(self, support_id or new_support_id())
        token = SUPPORT_OPERATION.set(current)
        try:
            yield current
        finally:
            current.active = False
            SUPPORT_OPERATION.reset(token)

    def support_details(self):
        current = self._current_operation()
        if current is None:
            return {}
        trace = self._current_trace()
        if trace is not None:
            current.trace_id = trace.span.trace_id
        result = {'support_id': current.support_id, 'report_state': current.report_state}
        for key in ('error_code', 'report_id', 'trace_id'):
            value = getattr(current, key)
            if value:
                result[key] = value
        return result

    def capture_failure(self, code, exc=None, *, source=None, stage=None, playback_backend=None, details=None):
        if code not in FAILURE_CODES:
            return None
        with self.operation() as operation:
            operation.error_code = code
            context = {**(details or {}), 'source': source, 'playback_backend': playback_backend}
            if stage is not None:
                context['stage'] = stage
            report_id = self.capture_exception(code, exc, details=context)
            if report_id:
                operation.report_id = report_id
                operation.report_state = 'queued'
            elif not reporting_enabled(self.config_path):
                operation.report_state = 'disabled'
            elif not self.configured:
                operation.report_state = 'unavailable'
            elif not operation.report_id:
                operation.report_state = 'limited'
            return report_id

    @contextmanager
    def trace(self, name):
        """Isolate concurrent operations and bind errors before background delivery."""
        if name not in TRACE_OPERATIONS or not self.traces_sample_rate or not self.can_send():
            yield None
            return
        stack = ExitStack()
        try:
            parent = self._current_trace()
            if parent is None:
                with self._lock:
                    if self._trace_client is None:
                        self._trace_client = self._make_client(TraceQueueTransport({'dsn': self.dsn}, self))
                scope = stack.enter_context(sentry_sdk.isolation_scope())
                scope.set_client(self._trace_client)
                span = sentry_sdk.start_transaction(name=name, op=TRACE_OPERATIONS[name])
            else:
                span = parent.span.start_child(op=TRACE_OPERATIONS[name], name=name)
            stack.enter_context(span)
        except Exception:
            try:
                stack.close()
            except Exception:
                pass
            yield None
            return
        with stack:
            current = _ActiveSpan(span, parent)
            token = self._active_span.set(current)
            try:
                yield span
            finally:
                current.active = False
                self._active_span.reset(token)

    def _current_trace(self):
        current = self._active_span.get()
        # asyncio tasks inherit context. A long-lived task must not keep using
        # a request's finished span after the request that created it returns.
        return current if current is not None and current.active and current.root.active else None

    def breadcrumb(self, event_type):
        if self.can_send() and isinstance(event_type, str) and _EVENT_TYPES.fullmatch(event_type):
            with self._lock:
                self._crumbs.append({'message': event_type})

    def _sanitize(self, event):
        safe = sanitize_report(event)
        if safe is None:
            return None
        # Also remove configured credentials if a provider accidentally puts one
        # in an otherwise allowed field, such as a request ID or code filename.
        cfg = configparser.ConfigParser(interpolation=None)
        try:
            cfg.read(self.config_path, encoding='utf-8')
            secrets = [value for section in cfg.sections() for key, value in cfg.items(section, raw=True)
                       if any(word in key.lower() for word in ('key', 'password', 'secret', 'token', 'cookie'))
                       or (section.lower() == 'events api' and key.lower() == 'url')]
        except (OSError, configparser.Error):
            return None
        for field in ('tags', 'fingerprint', 'exception', 'breadcrumbs', 'debug_meta'):
            if field in safe:
                safe[field] = sanitize_extraction_debug(safe[field], secrets)
        if safe.get('type') == 'transaction':
            safe = sanitize_extraction_debug(safe, secrets)
        else:
            safe['contexts']['failure'] = sanitize_extraction_debug(safe['contexts']['failure'], secrets)
            for context_name in ('trace', 'diagnostics'):
                if context_name in safe['contexts']:
                    safe['contexts'][context_name] = sanitize_extraction_debug(safe['contexts'][context_name], secrets)
        return sanitize_report(safe)

    def _write(self, record):
        encoded = json.dumps(record, ensure_ascii=True, allow_nan=False).encode('utf-8')
        if len(encoded) > MAX_REPORT_BYTES or not self.can_send():
            return False
        self.pending_dir.mkdir(parents=True, exist_ok=True)
        path = self.pending_dir / (record['event']['event_id'] + '.json')
        temporary = path.with_suffix('.tmp')
        temporary.write_bytes(encoded)
        temporary.replace(path)
        return True

    def capture_exception(self, event_type, exc=None, *, component='python', details=None, handled=True):
        if not self.can_send() or isinstance(exc, (KeyboardInterrupt, SystemExit)):
            return None
        values, seen = [], set()
        current = exc
        context = dict(details or {})
        while current is not None and id(current) not in seen and len(values) < 8:
            seen.add(id(current))
            frames = [{'filename': f.filename, 'function': f.name, 'lineno': f.lineno}
                      for f in traceback.extract_tb(current.__traceback__)]
            values.insert(0, {'type': type(current).__name__, 'stacktrace': {'frames': frames},
                              'mechanism': {'handled': handled}})
            response = getattr(current, 'response', None)
            for key in ('status_code', 'request_id'):
                value = getattr(current, key, None)
                if key == 'status_code' and value is None:
                    value = getattr(current, 'http_status', None) or getattr(response, 'status_code', None)
                if value is not None:
                    context.setdefault(key, value)
            current = current.__cause__ or (current.__context__ if not current.__suppress_context__ else None)
        event = {'tags': {'component': component, 'event_type': event_type},
                 'exception': {'values': values or [{'type': 'LoggedError'}]}, 'contexts': {'failure': context}}
        return self.capture_event(event)

    def capture_frontend(self, payload):
        if not isinstance(payload, dict):
            return None
        component = 'overlay' if payload.get('component') == 'overlay' else 'webui'
        error_type = payload.get('type')
        if error_type not in ('Error', 'TypeError', 'RangeError', 'ReferenceError', 'SyntaxError', 'URIError', 'EvalError'):
            error_type = 'JavaScriptError'
        return self.capture_event({'release': payload.get('release'),
                                   'tags': {'component': component, 'event_type': component + '.unhandled.error'},
                                   'exception': {'values': [{'type': error_type, 'mechanism': {'handled': False},
                                                            'stacktrace': {'frames': payload.get('frames', [])}}]}})

    def capture_event(self, event):
        try:
            with self._lock:
                if not self.can_send():
                    return None
                event = dict(event)
                operation = self._current_operation()
                if operation is not None:
                    event['tags'] = {**(event.get('tags') or {}), 'support_id': operation.support_id}
                cfg = configparser.ConfigParser(interpolation=None)
                cfg.read(self.config_path, encoding='utf-8')
                diagnostics = environment_for_report(cfg, self.release.removeprefix('tiptune@'), self._session_id)
                failure = (event.get('contexts') or {}).get('failure', {})
                if isinstance(failure, dict):
                    diagnostics.update(sanitize_environment({'playback_backend': failure.get('playback_backend')}))
                event['contexts'] = {**(event.get('contexts') or {}), 'diagnostics': diagnostics}
                current = self._current_trace()
                if current is not None and event.get('type') != 'transaction':
                    span = current.span
                    span.set_status('internal_error')
                    if span.containing_transaction is not None:
                        span.containing_transaction.set_status('internal_error')
                    event['contexts'] = {**(event.get('contexts') or {}), 'trace': span.get_trace_context()}
                    if operation is not None:
                        operation.trace_id = span.trace_id
                safe = self._sanitize(event)
                if safe is None:
                    return None
                transaction = safe.get('type') == 'transaction'
                signature = None if transaction else hashlib.sha256(json.dumps([
                    {key: value for key, value in safe['tags'].items() if key != 'support_id'},
                    safe['exception'], operation.support_id if operation is not None else None,
                ], sort_keys=True).encode()).hexdigest()
                now = time.time()
                if signature and now - self._seen.get(signature, 0) < 60:
                    return None
                self._seen = {key: value for key, value in self._seen.items() if now - value < 60}
                if signature and len(self._seen) >= MAX_REPORTS:
                    return None
                if not transaction:
                    safe['breadcrumbs'] = {'values': list(self._crumbs)}
                safe.setdefault('release', self.release)
                safe['environment'] = self.environment
                paths = sorted(self._paths(), key=lambda p: p.stat().st_mtime)
                # Reserve queue capacity for errors during outages; traces never evict them.
                if transaction and len(paths) >= MAX_REPORTS // 2:
                    return None
                for path in paths[:max(0, len(paths) - MAX_REPORTS + 1)]:
                    path.unlink(missing_ok=True)
                record = {'event': safe, 'created_at': now, 'attempts': 0, 'retry_at': 0}
                if not self._write(record):
                    return None
                if signature:
                    self._seen[signature] = now
                    if operation is not None and safe['tags']['event_type'] == operation.error_code:
                        operation.report_id = safe['event_id']
                        operation.report_state = 'queued'
                self._wake.set()
                return safe['event_id']
        except Exception:
            # Reporting must never change the outcome of the original operation.
            return None

    def _before_send(self, event, _hint):
        if not self.can_send():
            return None
        if _hint.get('tiptune_queued') and not _hint.get('tiptune_has_trace'):
            # The SDK creates a propagation context even in an empty scope.
            # That worker-only ID has no relationship to the original failure.
            event.get('contexts', {}).pop('trace', None)
        safe = self._sanitize(event)
        if safe is None:
            return None
        safe.setdefault('release', self.release)
        safe.setdefault('environment', self.environment)
        return safe

    def _make_client(self, transport):
        return sentry_sdk.Client(
            dsn=self.dsn, release=self.release, environment=self.environment,
            default_integrations=False, auto_enabling_integrations=False,
            include_local_variables=False, include_source_context=False,
            data_collection={'user_info': False, 'http_bodies': [], 'cookies': {'mode': 'off'},
                             'http_headers': {'request': {'mode': 'off'}, 'response': {'mode': 'off'}},
                             'url_query_params': {'mode': 'off'}, 'stack_frame_variables': False,
                             'frame_context_lines': 0, 'database_query_data': False, 'queues': False},
            auto_session_tracking=False, send_client_reports=False, enable_backpressure_handling=False,
            enable_logs=False, enable_metrics=False, traces_sample_rate=self.traces_sample_rate, profiles_sample_rate=0,
            propagate_traces=False, server_name='', before_send=self._before_send,
            before_send_transaction=self._before_send, _experiments={'max_spans': 50},
            transport=transport,
        )

    def _ensure_client(self):
        if self._client is None:
            self._transport = ConsentTransport({'dsn': self.dsn}, self)
            self._client = self._make_client(self._transport)

    def process_pending(self):
        """One retry pass; kept public for deterministic delivery/consent tests."""
        if not self.can_send():
            if not reporting_enabled(self.config_path):
                self.configure()
            return
        if time.time() < self._delivery_retry_at:
            return
        for path in sorted(self._paths(), key=lambda p: p.stat().st_mtime)[:MAX_REPORTS]:
            if path.suffix != '.json' or not self.can_send():
                continue
            try:
                if path.stat().st_size > MAX_REPORT_BYTES:
                    path.unlink(missing_ok=True)
                    continue
                record = json.loads(path.read_text(encoding='utf-8'))
                now = time.time()
                if not 0 <= now - record['created_at'] <= MAX_AGE_SECONDS:
                    path.unlink(missing_ok=True)
                    continue
                if record.get('retry_at', 0) > now:
                    continue
                _transport_active.value = True
                self._ensure_client()
                self._transport.delivered = False
                self._transport.discard = False
                event = sanitize_report(record['event'])
                if event is None:
                    path.unlink(missing_ok=True)
                    continue
                self._client.capture_event(event, hint={
                    'tiptune_queued': True, 'tiptune_has_trace': 'trace' in event.get('contexts', {}),
                }, scope=sentry_sdk.Scope())
                if self._transport.delivered:
                    path.unlink(missing_ok=True)
                    self.last_sent_at = now
                    self.delivery_error = None
                    continue
                if self._transport.discard:
                    path.unlink(missing_ok=True)
                    continue
                self.delivery_error = 'A report could not be sent. TipTune will retry later.'
            except Exception:
                self.delivery_error = 'A report could not be sent. TipTune will retry later.'
            finally:
                _transport_active.value = False
            try:
                with self._lock:
                    if not self.can_send():
                        continue
                    record['attempts'] = min(16, record.get('attempts', 0) + 1)
                    delay = min(3600, 5 * 2 ** record['attempts'])
                    record['retry_at'] = time.time() + max(delay, self._transport.retry_after if self._transport else 0)
                    record['event'] = sanitize_report(record['event'])
                    self._write(record)
                    if self._transport and self._transport.retry_after:
                        self._delivery_retry_at = record['retry_at']
                        break
            except Exception:
                path.unlink(missing_ok=True)

    def _run(self):
        while not self._stop.is_set():
            try:
                self.process_pending()
            except Exception:
                pass
            self._wake.wait(5)
            self._wake.clear()

    def close(self):
        self._stop.set()
        self._wake.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=1)
        if self._client:
            self._client.close(timeout=0)
        if self._trace_client:
            self._trace_client.close(timeout=0)


class ErrorReportingHandler(logging.Handler):
    def __init__(self, reporter):
        super().__init__(logging.INFO)
        self.reporter = reporter

    def emit(self, record):
        if getattr(_transport_active, 'value', False) or record.name.startswith('sentry_sdk'):
            return
        try:
            event_type = record.msg.get('event_type') if isinstance(record.msg, dict) else None
            if event_type:
                self.reporter.breadcrumb(event_type)
            if record.levelno >= logging.ERROR:
                exc = record.exc_info[1] if record.exc_info else None
                self.reporter.capture_exception(event_type or 'python.logging.error', exc)
        except Exception:
            pass

    def close(self):
        self.reporter.close()
        super().close()


def install_reporter(reporter):
    root = logging.getLogger()
    for handler in list(root.handlers):
        if isinstance(handler, ErrorReportingHandler):
            root.removeHandler(handler)
            handler.close()
    root.addHandler(ErrorReportingHandler(reporter))
    original_sys_hook = getattr(sys.excepthook, '_tiptune_original', sys.excepthook)
    original_thread_hook = getattr(threading.excepthook, '_tiptune_original', threading.excepthook)

    def sys_hook(kind, exc, tb):
        reporter.capture_exception('python.unhandled.error', exc, handled=False)
        original_sys_hook(kind, exc, tb)

    def thread_hook(args):
        reporter.capture_exception('python.thread.error', args.exc_value, handled=False)
        original_thread_hook(args)

    sys_hook._tiptune_original = original_sys_hook
    thread_hook._tiptune_original = original_thread_hook
    sys.excepthook = sys_hook
    threading.excepthook = thread_hook
