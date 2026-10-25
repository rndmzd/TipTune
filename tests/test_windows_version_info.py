"""Check the release metadata used to restrict backend signing requests."""

import json
from pathlib import Path
import sys
import tempfile
import unittest

from scripts.windows_version_info import make_version_info


@unittest.skipUnless(sys.platform == 'win32', 'Windows version resource')
class WindowsVersionInfoTests(unittest.TestCase):
    def test_backend_metadata_tracks_package_version(self):
        root = Path(__file__).resolve().parents[1]
        version = json.loads((root / 'package.json').read_text())['version']
        resource = str(make_version_info(root))
        self.assertIn("StringStruct('ProductName', 'TipTune')", resource)
        self.assertIn(f"StringStruct('ProductVersion', '{version}')", resource)
        self.assertIn(f"StringStruct('FileVersion', '{version}')", resource)
        self.assertIn("StringStruct('OriginalFilename', 'TipTune.exe')", resource)

    def test_rejects_versions_windows_cannot_represent(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for version in ('1.2.3-beta.1', '1.2', '1.2.65536'):
                with self.subTest(version=version):
                    (root / 'package.json').write_text(json.dumps({'version': version}))
                    with self.assertRaises(ValueError):
                        make_version_info(root)


if __name__ == '__main__':
    unittest.main()
