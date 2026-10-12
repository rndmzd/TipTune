"""JSON-safe, bounded song-extraction diagnostics without credentials."""

import re
import traceback
from types import SimpleNamespace


_SECRET_FIELDS = {
    'authorization', 'apikey', 'openaiapikey', 'googleapikey', 'key',
    'token', 'accesstoken', 'refreshtoken', 'clientsecret', 'password',
    'cookie', 'setcookie', 'headers',
}
_SECRET_TEXT = re.compile(
    r'(?i)(\b(?:api[_ -]?key|access[_ -]?token|refresh[_ -]?token|'
    r'client[_ -]?secret|password|authorization|token|key)\b["\']?\s*[:=]\s*["\']?)'
    r'[^&\s,"\'}]+'
)


def sanitize_extraction_debug(value, secrets=(), _depth=0):
    """Redact known credentials and common secret formats; cap large output."""
    if _depth > 10:
        return '[truncated]'
    if isinstance(value, str):
        for secret in secrets:
            if isinstance(secret, str) and secret:
                if len(secret) >= 4:
                    value = value.replace(secret, '[REDACTED]')
                else:
                    value = re.sub(r'(?<!\w)' + re.escape(secret) + r'(?!\w)', '[REDACTED]', value)
        value = re.sub(r'\bsk-[A-Za-z0-9_-]+', '[REDACTED]', value)
        value = re.sub(r'(?i)\bBearer\s+[A-Za-z0-9._~+/-]+=*', 'Bearer [REDACTED]', value)
        value = _SECRET_TEXT.sub(r'\1[REDACTED]', value)
        return value if len(value) <= 16000 else value[:16000] + '\n[truncated]'
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, dict):
        result = {}
        for key, item in list(value.items())[:100]:
            name = str(key)
            secret_field = re.sub(r'[^a-z]', '', name.lower()) in _SECRET_FIELDS
            result[name] = '[REDACTED]' if secret_field else sanitize_extraction_debug(item, secrets, _depth + 1)
        if len(value) > 100:
            result['_truncated'] = True
        return result
    if isinstance(value, (list, tuple)):
        result = [sanitize_extraction_debug(item, secrets, _depth + 1) for item in value[:50]]
        if len(value) > 50:
            result.append('[truncated]')
        return result
    if isinstance(value, SimpleNamespace):
        return sanitize_extraction_debug(vars(value), secrets, _depth + 1)
    if callable(getattr(value, 'model_dump', None)):
        try:
            return sanitize_extraction_debug(value.model_dump(mode='json'), secrets, _depth + 1)
        except Exception:
            return f'<{type(value).__name__}: serialization unavailable>'
    return f'<{type(value).__name__}>'


def extraction_exception_debug(exc, secrets=()):
    """Include causes, validation/API details, and stack frames, never locals."""
    chain, seen = [], set()
    current = exc
    while current is not None and id(current) not in seen and len(chain) < 8:
        seen.add(id(current))
        item = {'type': type(current).__name__, 'message': str(current)}
        for field in ('status_code', 'http_status', 'request_id', 'code', 'param', 'body'):
            value = getattr(current, field, None)
            if value is not None:
                item[field] = value
        response = getattr(current, 'response', None)
        if response is not None:
            item.setdefault('status_code', getattr(response, 'status_code', None))
            headers = getattr(response, 'headers', None)
            if callable(getattr(headers, 'get', None)):
                item.setdefault('request_id', headers.get('x-request-id'))
        if callable(getattr(current, 'errors', None)):
            try:
                item['validation_errors'] = current.errors(include_url=False)
            except Exception:
                pass
        chain.append(item)
        current = current.__cause__ or (current.__context__ if not current.__suppress_context__ else None)
    return sanitize_extraction_debug({
        'type': type(exc).__name__, 'message': str(exc), 'chain': chain,
        'traceback': ''.join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
    }, secrets)
