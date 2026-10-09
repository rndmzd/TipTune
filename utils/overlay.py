"""Cached viewer-facing overlay state. No playback or network operations live here."""

import asyncio
import copy
import re
import time
import uuid
from collections import deque


DEFAULTS = {
    'mode': 'browser', 'layout': 'full', 'position': 'bottom-left',
    'scale': '100', 'margin': '32', 'queue_length': '3',
    'show_now_playing': 'true', 'show_queue': 'true', 'show_alerts': 'true',
    'show_artwork': 'true', 'accent_color': '#5b8cff',
    'background_color': '#111318', 'text_color': '#f2f3f5',
    'opacity': '90', 'motion': 'subtle', 'source_name': 'TipTune Overlay',
}
CHOICES = {
    'mode': {'browser', 'text', 'off'}, 'layout': {'full', 'compact'},
    'position': {'bottom-left', 'bottom-right', 'top-left', 'top-right'},
    'motion': {'subtle', 'off'},
}
RANGES = {'scale': (50, 150), 'margin': (0, 200), 'queue_length': (1, 5), 'opacity': (0, 100)}
BOOL_KEYS = {'show_now_playing', 'show_queue', 'show_alerts', 'show_artwork'}
COLOR_KEYS = {'accent_color', 'background_color', 'text_color'}


def validate_settings(values):
    """Normalize a partial settings update, rejecting it before any INI writes."""
    out = {}
    for key, raw in values.items():
        if key not in DEFAULTS or raw is None:
            continue
        value = str(raw).strip()
        if key in CHOICES:
            value = value.lower()
            if value not in CHOICES[key]:
                raise ValueError(f'Invalid overlay {key.replace("_", " ")}.')
        elif key in RANGES:
            lo, hi = RANGES[key]
            try:
                number = int(value)
            except ValueError:
                raise ValueError(f'Overlay {key.replace("_", " ")} must be a whole number.') from None
            if not lo <= number <= hi:
                raise ValueError(f'Overlay {key.replace("_", " ")} must be between {lo} and {hi}.')
            value = str(number)
        elif key in BOOL_KEYS:
            if value.lower() not in {'true', 'false'}:
                raise ValueError(f'Invalid overlay {key.replace("_", " ")}.')
            value = value.lower()
        elif key in COLOR_KEYS:
            if not re.fullmatch(r'#[0-9a-fA-F]{6}', value):
                raise ValueError('Overlay colors must use six-digit hex colors, such as #5b8cff.')
            value = value.lower()
        elif key == 'source_name':
            if not value or len(value) > 100 or any(ord(c) < 32 for c in value):
                raise ValueError('Overlay source name must contain 1–100 printable characters.')
        out[key] = value
    return out


def read_settings(config):
    result = dict(DEFAULTS)
    # Existing installs keep their selected behavior until explicitly migrated.
    if config.sections() and not config.has_option('Overlay', 'mode'):
        try:
            enabled = config.getboolean('OBS', 'enabled', fallback=False)
        except ValueError:
            enabled = False
        result['mode'] = 'text' if enabled else 'off'
    if config.has_section('Overlay'):
        for key, value in config.items('Overlay'):
            try:
                result.update(validate_settings({key: value}))
            except ValueError:
                pass
    return result


def public_track(item):
    if not isinstance(item, dict):
        return None
    out = {}
    for key in ('queue_entry_id', 'source', 'name', 'album', 'requester'):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            out[key] = value.strip()[:500]
    artists = item.get('artists')
    if isinstance(artists, list):
        out['artists'] = [a[:300] for a in artists if isinstance(a, str) and a.strip()][:10]
    art = item.get('album_image_url')
    if isinstance(art, str) and art.startswith('https://'):
        out['album_image_url'] = art
    return out


class OverlayService:
    def __init__(self, config, clock=time.time):
        self.clock = clock
        self.session_id = uuid.uuid4().hex
        self.revision = 0
        self.config = read_settings(config)
        self.now_playing = None
        self.upcoming = []
        self.total_queue_count = 0
        self.status = {'queue_paused': False, 'playback_paused': False, 'starting': False}
        self.active = []
        self.pending = deque(maxlen=20)
        self.subscribers = set()

    def snapshot(self):
        return copy.deepcopy({
            'schema_version': 1, 'session_id': self.session_id,
            'revision': self.revision, 'server_timestamp': self.clock(),
            'config': self.config, 'now_playing': self.now_playing,
            'upcoming': self.upcoming, 'total_queue_count': self.total_queue_count,
            'status': self.status, 'alerts': self.active,
        })

    def publish(self):
        self.revision += 1
        state = self.snapshot()
        for subscriber in self.subscribers:
            if subscriber.full():
                subscriber.get_nowait()
            subscriber.put_nowait(state)

    def subscribe(self):
        # Register and enqueue the snapshot without yielding: no initial update race.
        subscriber = asyncio.Queue(maxsize=1)
        self.subscribers.add(subscriber)
        subscriber.put_nowait(self.snapshot())
        return subscriber

    def unsubscribe(self, subscriber):
        self.subscribers.discard(subscriber)

    def configure(self, config):
        settings = read_settings(config)
        if settings == self.config:
            return
        if settings['mode'] != self.config['mode']:
            self.active.clear()
            self.pending.clear()
        self.config = settings
        self.upcoming = self.upcoming[:int(settings['queue_length'])]
        self.publish()

    def update_tracks(self, now_playing, upcoming, status):
        current = public_track(now_playing)
        visible = [public_track(item) for item in upcoming[:int(self.config['queue_length'])]]
        flags = {key: bool(status.get(key)) for key in self.status}
        if (current, visible, len(upcoming), flags) != (
                self.now_playing, self.upcoming, self.total_queue_count, self.status):
            self.now_playing, self.upcoming = current, visible
            self.total_queue_count, self.status = len(upcoming), flags
            self.publish()

    def alert(self, kind, message, requester=None, duration=10):
        if self.config['mode'] != 'browser':
            return
        try:
            duration = max(1, min(300, float(duration)))
        except (ValueError, TypeError):
            duration = 10
        self.pending.append({
            'id': uuid.uuid4().hex, 'kind': kind, 'message': str(message)[:1000],
            'requester': str(requester)[:500] if requester else None,
            'created_at': self.clock(), 'duration': duration,
        })
        self.tick(force=True)

    def tick(self, force=False):
        now = self.clock()
        active = [item for item in self.active if item['expires_at'] > now]
        changed = len(active) != len(self.active)
        self.active = active
        while self.pending and self.pending[0]['created_at'] < now - 60:
            self.pending.popleft()
        while self.pending and len(self.active) < 3:
            item = self.pending.popleft()
            item['expires_at'] = now + item.pop('duration')
            self.active.append(item)
            changed = True
        if changed or force:
            self.publish()
