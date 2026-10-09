import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


class VersionBumpTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='tiptune-version-test-')
        self.root = Path(self.directory.name).resolve()
        self.assertEqual(self.root.parent, Path(tempfile.gettempdir()).resolve())
        self.addCleanup(self.directory.cleanup)
        (self.root / 'scripts').mkdir()
        (self.root / 'src-tauri').mkdir()
        shutil.copy2(Path(__file__).resolve().parents[1] / 'scripts/bump-minor.mjs', self.root / 'scripts')
        (self.root / 'package.json').write_text(json.dumps({'version': '1.3.0'}))
        (self.root / 'package-lock.json').write_text(json.dumps({'version': '1.3.0', 'packages': {
            '': {'version': '1.3.0'}, 'node_modules/example': {'version': '8.9.0'}}}))
        (self.root / 'src-tauri/tauri.conf.json').write_text('{\n  "version": "1.3.0"\n}\n')
        (self.root / 'src-tauri/Cargo.toml').write_text('[package]\nname = "tiptune-tauri"\nversion = "1.3.0"\n')
        (self.root / 'src-tauri/Cargo.lock').write_text('version = 4\n\n[[package]]\nname = "tiptune-tauri"\nversion = "1.3.0"\n\n[[package]]\nname = "example"\nversion = "8.9.0"\n')
        subprocess.run(['git', 'init', '--quiet', str(self.root)], check=True, capture_output=True)

    def run_bump(self, *args):
        return subprocess.run(['node', str(self.root / 'scripts/bump-minor.mjs'), '--allow-dirty', '--no-commit',
                               '--no-tag', *args], capture_output=True, text=True)

    def versions(self):
        return {str(path.relative_to(self.root)): path.read_bytes() for path in self.root.rglob('*')
                if path.is_file() and '.git' not in path.parts}

    def test_bump_updates_both_lockfiles_without_changing_dependencies(self):
        result = self.run_bump()
        self.assertEqual(result.returncode, 0, result.stderr)
        for name in ['package.json', 'package-lock.json', 'src-tauri/tauri.conf.json']:
            self.assertEqual(json.loads((self.root / name).read_text())['version'], '1.4.0')
        lock = json.loads((self.root / 'package-lock.json').read_text())
        self.assertEqual(lock['packages']['']['version'], '1.4.0')
        self.assertEqual(lock['packages']['node_modules/example']['version'], '8.9.0')
        self.assertIn('name = "tiptune-tauri"\nversion = "1.4.0"', (self.root / 'src-tauri/Cargo.lock').read_text())
        self.assertIn('name = "example"\nversion = "8.9.0"', (self.root / 'src-tauri/Cargo.lock').read_text())
        self.assertIn('version = "1.4.0"', (self.root / 'src-tauri/Cargo.toml').read_text())

    def test_dry_run_preserves_all_files(self):
        before = self.versions()
        result = self.run_bump('--dry-run')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(before, self.versions())

    def test_stale_lockfile_fails_before_writing_any_files(self):
        path = self.root / 'package-lock.json'
        lock = json.loads(path.read_text()); lock['packages']['']['version'] = '1.2.0'
        path.write_text(json.dumps(lock))
        before = self.versions()
        result = self.run_bump()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Version mismatch', result.stderr)
        self.assertEqual(before, self.versions())


if __name__ == '__main__':
    unittest.main()
