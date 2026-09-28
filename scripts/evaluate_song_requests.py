"""Opt-in, read-only comparison. Never imports app/helpers or initializes playback."""
import argparse
import configparser
import json
import logging
import os
from pathlib import Path
import statistics
import subprocess
import sys
import time
import types

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from openai import OpenAI
from spotipy import Spotify, SpotifyPKCE
from spotipy.cache_handler import MemoryCacheHandler
from chatdj.song_requests import SongExtractor, ExtractionOutput, normalize, DEFAULT_MODEL

BASELINE = '97939e2f6b00d63fb8e52b875eb75797f7f48bda'


class ReadOnlySpotify:
    """Expose only catalog/account reads to both implementations."""
    def __init__(self, client):
        self._client = client

    def me(self):
        return self._client.me()

    def track(self, *args, **kwargs):
        return self._client.track(*args, **kwargs)

    def search(self, *args, **kwargs):
        return self._client.search(*args, **kwargs)


class Meter:
    def __init__(self, client):
        self.calls = []
        for name in ('create', 'parse'):
            original = getattr(client.responses, name)
            def call(_original=original, **kwargs):
                started = time.perf_counter()
                entry = {'method': _original.__name__, 'model': kwargs.get('model')}
                try:
                    response = _original(**kwargs)
                    usage = getattr(response, 'usage', None)
                    entry['usage'] = usage.model_dump() if usage else None
                    entry['status'] = getattr(response, 'status', None)
                    return response
                except Exception as exc:
                    entry['error'] = safe_error(exc)
                    raise
                finally:
                    entry['seconds'] = round(time.perf_counter() - started, 3)
                    self.calls.append(entry)
            setattr(client.responses, name, call)


def safe_error(exc):
    # Do not serialize exception messages, request headers, URLs, or credentials.
    root = exc
    while root.__cause__ is not None:
        root = root.__cause__
    return {'type': type(root).__name__, 'status_code': getattr(root, 'status_code', None),
            'code': getattr(root, 'code', None)}


def legacy_module(ref):
    source = subprocess.check_output(['git', 'show', f'{ref}:chatdj/chatdj.py'], cwd=ROOT).decode('utf-8')
    module = types.ModuleType('tiptune_evaluation_baseline')
    sys.modules[module.__name__] = module
    exec(compile(source, f'<baseline {ref}>', 'exec'), module.__dict__)
    return module


def score(extracted, resolved, case):
    expected = case['expected']
    extraction_ok = len(extracted) == len(expected)
    catalog_ok = len(resolved) == len(expected)
    for index, item in enumerate(expected):
        if index >= len(extracted):
            extraction_ok = False
        else:
            actual = extracted[index]
            extraction_ok &= normalize(actual['song']) in [normalize(x) for x in item['titles']]
            if item['artist']:
                extraction_ok &= normalize(actual['artist']) == normalize(item['artist'])
        actual = resolved[index] if index < len(resolved) else None
        if actual is None:
            catalog_ok = False
        else:
            catalog_ok &= any(normalize(actual['song']) == normalize(pair[0]) and
                              normalize(actual['artist']) == normalize(pair[1])
                              for pair in item['acceptable_tracks'])
    return {'extraction_correct': bool(extraction_ok), 'catalog_correct': bool(catalog_ok)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', help='Explicitly enable paid API/catalog calls')
    parser.add_argument('--config', type=Path, default=Path(os.environ.get('TIPTUNE_CONFIG', ROOT/'config.ini')))
    parser.add_argument('--spotify-cache', type=Path)
    parser.add_argument('--model', default=DEFAULT_MODEL)
    parser.add_argument('--baseline-model', help='Defaults to saved model, or historical gpt-5-mini')
    parser.add_argument('--baseline-ref', default=BASELINE)
    parser.add_argument('--cases', type=Path, default=ROOT/'tests/fixtures/song_requests.json')
    parser.add_argument('--output', type=Path, default=ROOT/'build/song-request-evaluation.json')
    parser.add_argument('--limit', type=int)
    parser.add_argument('--implementation', choices=['both', 'old', 'new'], default='both',
                        help='Rerun one implementation without repeating an unchanged baseline')
    args = parser.parse_args()
    cases = json.loads(args.cases.read_text(encoding='utf-8'))
    if args.limit is not None:
        if args.limit <= 0:
            parser.error('--limit must be positive')
        cases = cases[:args.limit]
    if not args.live:
        print(json.dumps({'live': False, 'cases': len(cases), 'message': 'Pass --live to run API evaluation.'}))
        return
    logging.disable(logging.CRITICAL)
    report = {'baseline_ref': args.baseline_ref, 'model': args.model,
              'case_count': len(cases), 'status': 'blocked', 'preflight': {}, 'results': []}
    meters = {}
    try:
        config = configparser.ConfigParser()
        config.read(args.config)
        key = config.get('OpenAI', 'api_key', fallback='').strip() or os.environ.get('OPENAI_API_KEY', '')
        if not key or key == 'your-openai-api-key':
            raise RuntimeError('Missing API credentials')
        baseline_model = args.baseline_model or config.get('OpenAI', 'model', fallback='').strip() or 'gpt-5-mini'
        report['baseline_model'] = baseline_model
        new = SongExtractor(key, model=args.model)
        meters['new'] = Meter(new.openai_client)
        # Verify model access and strict schema support before running the suite.
        try:
            new._parse(ExtractionOutput, 'Return an empty songs list.', {'message': 'Thanks!'})
            report['preflight']['openai'] = 'passed'
        except Exception as exc:
            report['preflight']['openai'] = safe_error(exc)
            return
        cache = args.spotify_cache or args.config.with_name('.cache')
        token = json.loads(cache.read_text(encoding='utf-8'))
        auth = SpotifyPKCE(client_id=config.get('Spotify', 'client_id'),
            redirect_uri=config.get('Spotify', 'redirect_url'), open_browser=False,
            scope='user-modify-playback-state user-read-playback-state user-read-currently-playing user-read-private',
            cache_handler=MemoryCacheHandler(token_info=token), requests_timeout=15)
        if auth.validate_token(token) is None:
            raise RuntimeError('Spotify login required')
        spotify = ReadOnlySpotify(Spotify(auth_manager=auth, requests_timeout=15, retries=1))
        spotify.me()
        report['preflight']['spotify'] = 'passed'
        new.spotify_client = spotify
        new.google_api_key = config.get('Search', 'google_api_key', fallback='')
        new.google_cx = config.get('Search', 'google_cx', fallback='')
        legacy = legacy_module(args.baseline_ref)
        old = legacy.SongExtractor(key, spotify, new.google_api_key, new.google_cx, model=baseline_model)
        old.openai_client = OpenAI(api_key=key, timeout=30, max_retries=1)
        meters['old'] = Meter(old.openai_client)
        # Bypass AutoDJ.__init__: it controls playback and is forbidden during evaluation.
        old_dj = legacy.AutoDJ.__new__(legacy.AutoDJ)
        old_dj.spotify = spotify
        implementations = [('old', old), ('new', new)]
        if args.implementation != 'both':
            implementations = [(label, ex) for label, ex in implementations if label == args.implementation]
        for case in cases:
            for label, extractor in implementations:
                started = time.perf_counter()
                call_start = len(meters[label].calls)
                row = {'id': case['id'], 'implementation': label, 'extracted': [], 'resolved': []}
                try:
                    message = case['message']
                    if label == 'old' and len(message) < 3:
                        message = f'The song name might be "{message}".'
                    songs = extractor.extract_songs(message, case['count'])
                    if label == 'old' and not songs:
                        songs = [legacy.SongRequest(song=message, artist='')]
                    row['extracted'] = [s.model_dump() for s in songs]
                    for song in songs:
                        try:
                            if label == 'new':
                                resolved = extractor.resolve_spotify(song)
                                row['resolved'].append(resolved.model_dump() if resolved else None)
                            else:
                                uri = song.spotify_uri or old_dj.search_track_uri(song.song, song.artist)
                                info = spotify.track(uri) if uri else None
                                row['resolved'].append({'song': info['name'],
                                    'artist': ', '.join(a['name'] for a in info['artists']),
                                    'spotify_uri': uri} if info else None)
                        except Exception as exc:
                            row['resolved'].append(None)
                            row.setdefault('lookup_errors', []).append(safe_error(exc))
                    row.update(score(row['extracted'], row['resolved'], case))
                except Exception as exc:
                    row.update(error=safe_error(exc), extraction_correct=False, catalog_correct=False)
                row['seconds'] = round(time.perf_counter() - started, 3)
                row['api_calls'] = meters[label].calls[call_start:]
                report['results'].append(row)
                print(json.dumps({k: row.get(k) for k in ('id', 'implementation', 'extraction_correct', 'catalog_correct', 'seconds')}), flush=True)
        report['status'] = 'completed'
        report['summary'] = {}
        for label, _ in implementations:
            rows = [r for r in report['results'] if r['implementation'] == label]
            calls = [c for r in rows for c in r['api_calls']]
            report['summary'][label] = {
                'cases': len(rows), 'extraction_correct': sum(r['extraction_correct'] for r in rows),
                'catalog_correct': sum(r['catalog_correct'] for r in rows),
                'service_failures': sum(bool(r.get('error') or r.get('lookup_errors') or
                                             any(c.get('error') for c in r['api_calls'])) for r in rows),
                'unresolved_requests': sum(sum(item is None for item in r['resolved']) for r in rows),
                'median_seconds': round(statistics.median(r['seconds'] for r in rows), 3),
                'api_calls': len(calls),
                'input_tokens': sum((c.get('usage') or {}).get('input_tokens', 0) for c in calls),
                'output_tokens': sum((c.get('usage') or {}).get('output_tokens', 0) for c in calls)}
    except Exception as exc:
        report['blocker'] = safe_error(exc)
    finally:
        report['preflight_calls'] = {label: meter.calls for label, meter in meters.items()} if not report['results'] else {}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps({'status': report['status'], 'report': str(args.output),
                          'preflight': report['preflight'], 'summary': report.get('summary')}), flush=True)


if __name__ == '__main__':
    main()
