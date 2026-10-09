"""Isolated, illustrative backend for overlay browser tests. Never uses user credentials."""
import asyncio
import configparser
import logging
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

from aiohttp import web

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


async def main():
    with tempfile.TemporaryDirectory(prefix='tiptune-overlay-qa-') as directory:
        root = Path(directory)
        os.environ.update(TIPTUNE_CONFIG=str(root / 'config.ini'), TIPTUNE_CACHE_DIR=str(root / 'cache'),
                          TIPTUNE_DEFAULT_LOG_PATH=str(root / 'qa.log'))
        import app
        import helpers
        app.config = configparser.ConfigParser()
        app.config.read_dict({'General': {'setup_complete': 'true', 'song_cost': '27', 'request_overlay_duration': '10'},
            'OBS': {'enabled': 'false'}, 'Overlay': {'mode': 'browser'}, 'Music': {'source': 'spotify'}})
        with (root / 'config.ini').open('w', encoding='utf-8') as handle:
            app.config.write(handle)
        logging.disable(logging.CRITICAL)
        app._setup_logging = lambda: None
        helpers.refresh_spotify_client = lambda: None
        with patch.object(app, 'Actions', return_value=SimpleNamespace(chatdj_enabled=False, obs_integration_enabled=False)):
            service = app.SongRequestService()
        original = [
            {'queue_entry_id': 'current', 'source': 'spotify', 'name': 'Midnight City', 'artists': ['M83'], 'requester': 'StarGazer'},
            {'queue_entry_id': 'upcoming-1', 'source': 'youtube', 'name': 'A Moment Apart', 'artists': ['ODESZA'], 'requester': 'Moonwalker'},
            {'queue_entry_id': 'upcoming-2', 'source': 'spotify', 'name': 'Dreams', 'artists': ['Fleetwood Mac'], 'requester': 'NightOwl'},
            {'queue_entry_id': 'upcoming-3', 'source': 'spotify', 'name': 'Something About Us', 'artists': ['Daft Punk']},
        ]
        server = app.WebUI(service, port=int(os.environ.get('TIPTUNE_QA_PORT', '18765')))
        service._web = server

        async def control(request):
            payload = await request.json()
            service._queue_now_playing = payload.get('now_playing', dict(original[0]))
            service._queue_items = payload.get('upcoming', [dict(x) for x in original[1:]])
            service._queue_paused = bool(payload.get('queue_paused', False))
            service._queue_playback_paused = bool(payload.get('playback_paused', False))
            service.overlay.active.clear(); service.overlay.pending.clear()
            app.config['Overlay'].update({'mode': 'browser', 'layout': 'full', 'position': 'bottom-left',
                'scale': '100', 'queue_length': '3', 'show_now_playing': 'true', 'show_queue': 'true',
                'show_alerts': 'true', 'show_artwork': 'true', 'motion': 'subtle'})
            service.overlay.configure(app.config)
            service._overlay_sync()
            return web.json_response({'ok': True})

        server._app.router.add_post('/__test/state', control)
        service._queue_now_playing = dict(original[0]); service._queue_items = [dict(x) for x in original[1:]]
        service._overlay_sync()
        await server.start()
        async def tick():
            while True:
                service.overlay.tick()
                await asyncio.sleep(.1)
        task = asyncio.create_task(tick())
        print(f'Overlay QA server: {server.overlay_url}', flush=True)
        try:
            await asyncio.Event().wait()
        finally:
            task.cancel(); await asyncio.gather(task, return_exceptions=True)
            await server.stop()


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
