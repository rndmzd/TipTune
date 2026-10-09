"""Run isolated Windows lifecycle checks against a built PyInstaller backend."""
import argparse
import configparser
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--backend', type=Path, required=True)
parser.add_argument('--output', type=Path, default=Path('output/overlay-qa/shutdown/packaged-check'))
args = parser.parse_args()
if sys.platform != 'win32':
    parser.error('These lifecycle checks require Windows.')
root = args.output.resolve()
backend = args.backend.resolve(strict=True)
repo = Path.cwd()
harness = max((repo / 'src-tauri/target/debug/deps').glob('windows_lifecycle-*.exe'), key=lambda p: p.stat().st_mtime)
with socket.socket() as check:
    check.bind(('127.0.0.1', 0))
    port = check.getsockname()[1]


def processes():
    environment = os.environ.copy()
    environment.pop('TIPTUNE_WINDOWS_JOB_NAME', None)
    environment.pop('TIPTUNE_PARENT_PID', None)
    environment['TIPTUNE_TEST_QA_EXE'] = str(backend)
    result = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command',
        "Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -eq $env:TIPTUNE_TEST_QA_EXE } | Select-Object ProcessId,ParentProcessId | ConvertTo-Json -Compress"],
        env=environment, capture_output=True, text=True, check=True, creationflags=subprocess.CREATE_NO_WINDOW)
    if not result.stdout.strip():
        return []
    data = json.loads(result.stdout)
    return data if isinstance(data, list) else [data]


def case_environment(directory):
    directory.mkdir(parents=True, exist_ok=True)
    cfg = configparser.ConfigParser()
    cfg.read_dict({'General': {'setup_complete': 'true', 'song_cost': '27', 'debug_log_to_file': 'true',
                              'debug_log_path': str(directory / 'backend.log')}, 'Music': {'source': 'youtube'},
                   'OBS': {'enabled': 'false'}, 'Overlay': {'mode': 'browser'}, 'Events API': {'url': ''}})
    with (directory / 'config.ini').open('w', encoding='utf-8') as handle:
        cfg.write(handle)
    environment = os.environ.copy()
    for key in ('TIPTUNE_WINDOWS_JOB_NAME', 'TIPTUNE_PARENT_PID', 'TIPTUNE_LOG_PATH',
                'TIPTUNE_LOG_PATH_FORCE', 'TIPTUNE_LOG_LEVEL', 'TIPTUNE_LOG_LEVEL_FORCE'):
        environment.pop(key, None)
    environment.update(TIPTUNE_CONFIG=str(directory / 'config.ini'), TIPTUNE_CACHE_DIR=str(directory / 'cache'),
                       TIPTUNE_DEFAULT_LOG_PATH=str(directory / 'backend.log'), TIPTUNE_WEB_PORT=str(port),
                       TIPTUNE_WEB_HOST='127.0.0.1', TIPTUNE_TEST_BACKEND_EXE=str(backend))
    return environment


def wait_stopped():
    deadline = time.monotonic() + 8
    while processes():
        assert time.monotonic() < deadline, 'A packaged backend process survived shutdown'
        time.sleep(.1)
    with socket.socket() as check:
        check.bind(('127.0.0.1', port))


results = []
for mode in ('normal', 'updater', 'forced'):
    directory = root / mode
    ready, gate = directory / 'ready.json', directory / 'exit.gate'
    ready.unlink(missing_ok=True); gate.unlink(missing_ok=True)
    environment = case_environment(directory)
    environment.update(TIPTUNE_TEST_READY=str(ready), TIPTUNE_TEST_GATE=str(gate), TIPTUNE_TEST_EXIT_MODE=mode)
    owner = subprocess.Popen([str(harness), '--ignored', '--exact', 'owner_fixture'], env=environment,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        deadline = time.monotonic() + 20
        while True:
            try:
                with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/overlay/state', timeout=2) as response:
                    assert json.load(response)['state']['schema_version'] == 1
                break
            except OSError:
                assert time.monotonic() < deadline and owner.poll() is None, 'Packaged backend did not start'
                time.sleep(.1)
        members = processes()
        assert len(members) == 2, members  # PyInstaller launcher and application worker
        if mode == 'forced':
            owner.kill()
        else:
            gate.write_text('exit', encoding='utf-8')
        owner.wait(timeout=8)
        wait_stopped()
        results.append({'case': mode, 'pyinstaller_processes': len(members), 'all_stopped': True, 'port_released': True})
        print(f'{mode}: both PyInstaller processes exited and the HTTP port was released.', flush=True)
    finally:
        if owner.poll() is None:
            owner.kill(); owner.wait(timeout=5)
        ready.unlink(missing_ok=True); gate.unlink(missing_ok=True)

# The desktop can disappear before the worker opens its named job. It must exit
# without showing a windowed PyInstaller traceback dialog or starting services.
environment = case_environment(root / 'missing-owner')
environment['TIPTUNE_WINDOWS_JOB_NAME'] = f'Local\\TipTune-owner-gone-{os.getpid()}'
process = subprocess.Popen([str(backend)], env=environment, creationflags=subprocess.CREATE_NO_WINDOW)
try:
    assert process.wait(timeout=8) == 1
    wait_stopped()
    print('Missing owner: packaged startup exited without a traceback dialog or surviving processes.', flush=True)
finally:
    if process.poll() is None:
        subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True)
        process.wait(timeout=5)

# A duplicate server must fail immediately, without background tip processing.
environment = case_environment(root / 'busy-port')
with socket.socket() as occupied:
    occupied.bind(('127.0.0.1', port)); occupied.listen()
    process = subprocess.Popen([str(backend)], env=environment, creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        assert process.wait(timeout=8) == 1
        assert not processes()
        assert 'webui.error' in (root / 'busy-port' / 'backend.log').read_text(encoding='utf-8')
        print('Busy port: packaged startup exited with code 1 and left no backend processes.', flush=True)
    finally:
        if process.poll() is None:
            subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True)
            process.wait(timeout=5)
(root / 'packaged-results.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
