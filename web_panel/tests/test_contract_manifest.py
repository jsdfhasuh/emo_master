"""Verify current generated files, the installed SDK, and embedded fixtures."""
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import unittest
import zipfile

PANEL = Path(__file__).resolve().parents[1]
CONTRACT = PANEL / 'contracts/panel-contract-v1'
class ContractManifestTests(unittest.TestCase):
    def test_current_generated_files_match_manifest(self):
        manifest = json.loads((CONTRACT / 'contract-manifest.json').read_bytes())
        sdk = version('emo-panel-protocol')
        self.assertRegex(manifest['sourceSha'], r'^[0-9a-f]{40}$')
        self.assertEqual(manifest['sdkVersion'], sdk)
        self.assertEqual((manifest['projectSchemaVersion'], manifest['presentationSchemaVersion']), ('2.10', '1.3'))
        self.assertEqual(manifest['panelContractVersion'], '1.0')
        wheel, = manifest['sdkWheels']
        self.assertEqual(wheel, f'python/emo_panel_protocol-{sdk}-py3-none-any.whl')
        expected_files = set(manifest['files']) | {'contract-manifest.json'}
        self.assertEqual({p.relative_to(CONTRACT).as_posix() for p in CONTRACT.rglob('*') if p.is_file()}, expected_files)
        for name, expected in manifest['files'].items():
            with self.subTest(file=name):
                data = (CONTRACT / name).read_bytes()
                self.assertEqual(len(data), expected['byteSize'])
                self.assertEqual(hashlib.sha256(data).hexdigest(), expected['sha256'])
        with zipfile.ZipFile(CONTRACT / wheel) as archive:
            self.assertIsNone(archive.testzip())
            from emo_panel_protocol import __version__
            from emo_panel_protocol.generated import runtime_pb2
            self.assertEqual(__version__, sdk)
            self.assertEqual(archive.read('emo_panel_protocol/generated/runtime_pb2.py'),
                             Path(runtime_pb2.__file__).read_bytes())
            self.assertFalse(any(name.startswith('emo_master/') for name in archive.namelist()))

    def test_embedded_fixtures_are_the_same_sdk_delivery(self):
        embedded = PANEL / 'backend/emo_web_panel/contract_data'
        fixtures = {p.name for p in (CONTRACT / 'fixtures').glob('*.json')}
        self.assertEqual({p.name for p in (embedded / 'fixtures').glob('*.json')}, fixtures)
        for name in fixtures:
            with self.subTest(fixture=name):
                self.assertEqual((embedded / 'fixtures' / name).read_bytes(), (CONTRACT / 'fixtures' / name).read_bytes())
        self.assertEqual((embedded / 'demo.png').read_bytes(), (CONTRACT / 'sample-panels/source/demo.png').read_bytes())


if __name__ == '__main__':
    unittest.main()
