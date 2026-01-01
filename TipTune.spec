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


def _datas():
    root = ROOT
    items = []

    def add(rel_path: str, dest: str = '.'):
        src = os.path.join(root, rel_path)
        if os.path.exists(src):
            items.append((src, dest))

    add('scenes.yaml', '.')
    add('config.ini.example', '.')
    add('webui', 'webui')

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
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
