# -*- mode: python ; coding: utf-8 -*-

import os

from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

hiddenimports = []
hiddenimports += collect_submodules('aiohttp')
hiddenimports += collect_submodules('httpx')
hiddenimports += collect_submodules('spotipy')
hiddenimports += collect_submodules('simpleobsws')


ROOT = os.path.abspath(SPECPATH)

SIDECAR_CONSOLE = os.environ.get('TIPTUNE_SIDECAR_CONSOLE', '').strip() == '1'


def _datas():
    root = ROOT
    items = []

    def add(rel_path: str, dest: str = '.'):
        src = os.path.join(root, rel_path)
        if os.path.exists(src):
            items.append((src, dest))

    def add_dir(rel_src_dir: str, rel_dest_dir: str):
        src_dir = os.path.join(root, rel_src_dir)
        if not os.path.isdir(src_dir):
            return
        for base, dirs, files in os.walk(src_dir):
            dirs[:] = [d for d in dirs if d not in ('node_modules', '.git')]
            rel_base = os.path.relpath(base, src_dir)
            dest = os.path.join(rel_dest_dir, rel_base) if rel_base != '.' else rel_dest_dir
            for fn in files:
                items.append((os.path.join(base, fn), dest))

    add('scenes.yaml', '.')
    add('config.ini.example', '.')
    add('docs/USER_MANUAL.md', 'docs')
    add('docs/QUICK_START.md', 'docs')
    add_dir('webui/dist', 'webui/dist')

    return items


a = Analysis(
    ['app.py'],
    pathex=[ROOT],
    binaries=[],
    datas=_datas(),
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='TipTune',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=SIDECAR_CONSOLE,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
