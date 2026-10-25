"""Create credential-free build metadata while packaging, never at app startup."""

import hashlib
from pathlib import Path
import subprocess

from utils.support_diagnostics import dependency_versions, sanitize_environment


def create_build_info(root):
    root = Path(root)
    result = {'build_kind': 'packaged', 'dependencies': dependency_versions()}
    try:
        commit = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=root, capture_output=True,
                                text=True, timeout=5, check=True).stdout.strip()
        dirty = subprocess.run(['git', 'status', '--porcelain', '--untracked-files=no'], cwd=root,
                               capture_output=True, text=True, timeout=5, check=True).stdout
        result.update(commit=commit, source_dirty=bool(dirty.strip()))
    except (OSError, subprocess.SubprocessError):
        pass
    digest = hashlib.sha256()
    paths = [root / name for name in ('app.py', 'package.json', 'sentry.json', 'TipTune.spec')]
    for folder in ('utils', 'helpers', 'handlers', 'chatdj', 'webui/dist'):
        paths.extend((root / folder).rglob('*'))
    for path in sorted(set(paths)):
        if path.is_file() and path.suffix in ('.py', '.json', '.spec', '.js', '.css', '.html'):
            digest.update(path.relative_to(root).as_posix().encode('utf-8'))
            digest.update(b'\0')
            digest.update(path.read_bytes())
            digest.update(b'\0')
    result['build_id'] = digest.hexdigest()
    return sanitize_environment(result)
