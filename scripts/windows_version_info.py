"""Windows publisher/product metadata for the frozen TipTune backend."""

import json
from pathlib import Path
import re


def make_version_info(root):
    from PyInstaller.utils.win32.versioninfo import (
        FixedFileInfo, StringFileInfo, StringStruct, StringTable,
        VarFileInfo, VarStruct, VSVersionInfo,
    )

    version = json.loads((Path(root) / 'package.json').read_text(encoding='utf-8'))['version']
    if not re.fullmatch(r'\d+\.\d+\.\d+', version):
        raise ValueError('Windows releases require a stable x.y.z version')
    numeric = tuple(map(int, version.split('.'))) + (0,)
    if any(part > 65535 for part in numeric):
        raise ValueError('Windows version components must fit in 16 bits')
    return VSVersionInfo(
        ffi=FixedFileInfo(filevers=numeric, prodvers=numeric, mask=0x3F, flags=0,
                          OS=0x40004, fileType=1, subtype=0, date=(0, 0)),
        kids=[
            StringFileInfo([StringTable('040904B0', [
                StringStruct('CompanyName', 'TipTune'),
                StringStruct('FileDescription', 'TipTune backend'),
                StringStruct('FileVersion', version),
                StringStruct('InternalName', 'TipTune'),
                StringStruct('OriginalFilename', 'TipTune.exe'),
                StringStruct('ProductName', 'TipTune'),
                StringStruct('ProductVersion', version),
            ])]),
            VarFileInfo([VarStruct('Translation', [1033, 1200])]),
        ],
    )
