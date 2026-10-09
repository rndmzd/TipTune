"""Verify the desktop payload in Windows MSI and NSIS installers."""

import argparse
from pathlib import Path
import shutil
import subprocess
import tempfile


MARKERS = {'msi': b'__TAURI_BUNDLE_TYPE_VAR_MSI', 'nsis': b'__TAURI_BUNDLE_TYPE_VAR_NSS'}
UNKNOWN = b'__TAURI_BUNDLE_TYPE_VAR_UNK'


def check_installer(installer: Path, expected: str, seven_zip: str):
    # The CLI restores the original, unknown marker in target/release after bundling.
    # Check the actual installer payload instead of that unbundled build executable.
    parent = installer.resolve().parent
    with tempfile.TemporaryDirectory(prefix='tiptune-bundle-metadata-', dir=parent) as directory:
        extracted = Path(directory).resolve()
        assert extracted.parent == parent
        subprocess.run([seven_zip, 'x', str(installer.resolve()), f'-o{extracted}', '-y'],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        payloads = []
        for path in extracted.rglob('*'):
            if not path.is_file():
                continue
            with path.open('rb') as handle:
                if handle.read(2) != b'MZ':
                    continue
            data = path.read_bytes()
            if b'__TAURI_BUNDLE_TYPE_VAR_' not in data:
                continue
            payloads.append(path)
            if UNKNOWN in data:
                raise ValueError(f'{installer.name}: bundle metadata is unknown. Align the CLI and Rust dependencies.')
            found = [kind for kind, marker in MARKERS.items() if marker in data]
            if found != [expected]:
                raise ValueError(f'{installer.name}: expected {expected} metadata, found {found}.')
        if len(payloads) != 1:
            raise ValueError(f'{installer.name}: expected one desktop payload with bundle metadata, found {len(payloads)}.')
    print(f'Installer payload reports {expected}: {installer.name}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sources = parser.add_mutually_exclusive_group(required=True)
    sources.add_argument('--bundle-dir', type=Path)
    sources.add_argument('--installer', type=Path)
    parser.add_argument('--expected', choices=MARKERS)
    parser.add_argument('--seven-zip', default=shutil.which('7z') or r'C:\Program Files\7-Zip\7z.exe')
    args = parser.parse_args()
    if not Path(args.seven_zip).is_file():
        parser.error('7-Zip is required to inspect installer payloads. Install it or pass --seven-zip.')
    try:
        if args.installer:
            if not args.expected:
                parser.error('--installer requires --expected msi or nsis.')
            check_installer(args.installer, args.expected, args.seven_zip)
        else:
            for kind, pattern in [('msi', '*.msi'), ('nsis', '*.exe')]:
                installers = list((args.bundle_dir / kind).glob(pattern))
                if not installers:
                    raise ValueError(f'No {kind} installer found in {args.bundle_dir}.')
                check_installer(max(installers, key=lambda path: path.stat().st_mtime), kind, args.seven_zip)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        parser.error(str(exc))


if __name__ == '__main__':
    main()
