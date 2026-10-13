"""Opt-in, bounded error reports. Application data never enters the upload queue."""

import configparser
from collections import deque
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


MAX_REPORTS = 100
MAX_REPORT_BYTES = 64 * 1024
MAX_AGE_SECONDS = 7 * 24 * 60 * 60
_REPORT_NAME = re.compile(r'^[a-f0-9]{32}\.(?:json|tmp)$')
_ATOM = re.compile(r'^[A-Za-z0-9_.:-]{1,128}$')
_EVENT_TYPES = re.compile(r'^[a-z][a-z0-9_.]{0,127}$')
_ASSET_PATH = re.compile(r'^app:///assets/[A-Za-z0-9_.-]{1,128}\.js$')
_DEBUG_ID = re.compile(r'^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$')
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


def sanitize_report(event):
    """Allowlist the complete event, including events restored from disk or the UI.

    Free-form log/exception messages are deliberately replaced: they can contain
    usernames, request text, authenticated URLs, or provider response bodies.
    """
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
        # Do not send sessions, transactions, logs, metrics, or attachments.
        if len(envelope.items) != 1 or envelope.items[0].type != 'event':
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


class ErrorReporter:
    def __init__(self, config_path, cache_dir, dsn=None, environment=None, *, start_worker=True):
        self.config_path = Path(config_path)
        self.pending_dir = Path(cache_dir) / 'error-reports' / 'pending'
        destination, default_environment = load_destination()
        self.dsn = destination if dsn is None else dsn
        self.environment = _atom(environment or default_environment, 'production')
        self.release = 'tiptune@' + app_version()
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
            self._enabled = reporting_enabled(self.config_path)
            if not self._enabled:
                self._crumbs.clear()
                self._seen.clear()
                self.delivery_error = None
                self._purge()
                self._delivery_retry_at = 0
            elif self.configured and self._start_worker and self._thread is None:
                self._thread = threading.Thread(target=self._run, name='TipTune error reports', daemon=True)
                self._thread.start()
            self._wake.set()

    def status(self):
        with self._lock:
            return {'enabled': reporting_enabled(self.config_path), 'configured': self.configured,
                    'pending': len([p for p in self._paths() if p.suffix == '.json']),
                    'last_sent_at': self.last_sent_at, 'error': self.delivery_error}

    def breadcrumb(self, event_type):
        if self.can_send() and isinstance(event_type, str) and _EVENT_TYPES.fullmatch(event_type):
            with self._lock:
                self._crumbs.append({'message': event_type})

    def _sanitize(self, event):
        safe = sanitize_report(event)
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
        safe['contexts']['failure'] = sanitize_extraction_debug(safe['contexts']['failure'], secrets)
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
            for key in ('status_code', 'request_id'):
                value = getattr(current, key, None)
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
                safe = self._sanitize(event)
                if safe is None:
                    return None
                signature = hashlib.sha256(json.dumps([safe['tags'], safe['exception']], sort_keys=True).encode()).hexdigest()
                now = time.time()
                if now - self._seen.get(signature, 0) < 60:
                    return None
                self._seen = {key: value for key, value in self._seen.items() if now - value < 60}
                if len(self._seen) >= MAX_REPORTS:
                    return None
                safe['breadcrumbs'] = {'values': list(self._crumbs)}
                safe.setdefault('release', self.release)
                safe['environment'] = self.environment
                paths = sorted(self._paths(), key=lambda p: p.stat().st_mtime)
                for path in paths[:max(0, len(paths) - MAX_REPORTS + 1)]:
                    path.unlink(missing_ok=True)
                record = {'event': safe, 'created_at': now, 'attempts': 0, 'retry_at': 0}
                if not self._write(record):
                    return None
                self._seen[signature] = now
                self._wake.set()
                return safe['event_id']
        except Exception:
            # Reporting must never change the outcome of the original operation.
            return None

    def _before_send(self, event, _hint):
        if not self.can_send():
            return None
        safe = self._sanitize(event)
        if safe is None:
            return None
        safe.setdefault('release', self.release)
        safe.setdefault('environment', self.environment)
        return safe

    def _ensure_client(self):
        if self._client is None:
            self._transport = ConsentTransport({'dsn': self.dsn}, self)
            self._client = sentry_sdk.Client(
                dsn=self.dsn, release=self.release, environment=self.environment,
                default_integrations=False, auto_enabling_integrations=False,
                include_local_variables=False, include_source_context=False,
                data_collection={'user_info': False, 'http_bodies': [], 'cookies': {'mode': 'off'},
                                 'http_headers': {'request': {'mode': 'off'}, 'response': {'mode': 'off'}},
                                 'url_query_params': {'mode': 'off'}, 'stack_frame_variables': False,
                                 'frame_context_lines': 0, 'database_query_data': False, 'queues': False},
                auto_session_tracking=False, send_client_reports=False, enable_backpressure_handling=False,
                enable_logs=False, enable_metrics=False, traces_sample_rate=0, profiles_sample_rate=0,
                propagate_traces=False, server_name='', before_send=self._before_send,
                transport=self._transport,
            )

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
                self._client.capture_event(sanitize_report(record['event']), scope=sentry_sdk.Scope())
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
