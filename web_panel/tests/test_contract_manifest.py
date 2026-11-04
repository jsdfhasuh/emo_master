"""Pin SDK 1.5.0 development delivery and embedded consumer bytes on both OSes."""
import hashlib
import json
from pathlib import Path
import unittest
import zipfile

PANEL = Path(__file__).resolve().parents[1]
CONTRACT = PANEL / 'contracts/panel-contract-v1'
HANDOFF = PANEL.parent / 'docs/handoffs/runtime-web-panel-v1/sdk-1.5.0'
SOURCE_SHA = 'd92ffd37960e8b8f527136151ab9ccb80054e78f'
WHEEL_SHA = '97db049bbbc86152f899af809c4e670b582e345b061f6a3e439d6722f5d33a6d'
WHEEL_PATH = 'python/emo_panel_protocol-1.5.0-py3-none-any.whl'


class ContractManifestTests(unittest.TestCase):
    def test_all_authoritative_files_match_development_handoff(self):
        manifest = json.loads((CONTRACT / 'contract-manifest.json').read_bytes())
        self.assertEqual(manifest['sourceSha'], SOURCE_SHA)
        self.assertTrue(manifest['sourceDirty'])
        self.assertEqual(manifest['sdkVersion'], '1.5.0')
        self.assertEqual(manifest['panelContractVersion'], '1.0')
        self.assertEqual(manifest['sdkWheels'], [WHEEL_PATH])
        self.assertEqual(len(manifest['files']), 40)
        expected_files = set(manifest['files']) | {'contract-manifest.json'}
        self.assertEqual({p.relative_to(CONTRACT).as_posix() for p in CONTRACT.rglob('*') if p.is_file()}, expected_files)
        for name, expected in manifest['files'].items():
            with self.subTest(file=name):
                data = (CONTRACT / name).read_bytes()
                self.assertEqual(len(data), expected['byteSize'])
                self.assertEqual(hashlib.sha256(data).hexdigest(), expected['sha256'])
        wheel = (CONTRACT / WHEEL_PATH).read_bytes()
        self.assertEqual(len(wheel), 40265)
        self.assertEqual(hashlib.sha256(wheel).hexdigest(), WHEEL_SHA)

    def test_vendored_tree_is_exact_current_handoff_manifest_and_generated_sdk(self):
        self.assertEqual(json.loads((HANDOFF / 'contract-manifest.json').read_bytes()),
                         json.loads((CONTRACT / 'contract-manifest.json').read_bytes()))
        self.assertEqual((CONTRACT / 'proto/runtime.proto').read_bytes(),
                         (PANEL.parent / 'proto/runtime.proto').read_bytes())
        with zipfile.ZipFile(CONTRACT / WHEEL_PATH) as archive:
            self.assertIsNone(archive.testzip())
            for name in ('runtime_pb2.py','runtime_pb2_grpc.py'):
                self.assertEqual(archive.read('emo_panel_protocol/generated/' + name),
                    (PANEL.parent / 'src/emo_panel_protocol/generated' / name).read_bytes())
            self.assertFalse(any(name.startswith('emo_master/') for name in archive.namelist()))

    def test_embedded_fixtures_are_the_same_sdk_delivery(self):
        embedded = PANEL / 'backend/emo_web_panel/contract_data'
        fixtures = {p.name for p in (CONTRACT / 'fixtures').glob('*.json')}
        self.assertEqual({p.name for p in (embedded / 'fixtures').glob('*.json')}, fixtures)
        self.assertEqual(len(fixtures), 24)
        for name in fixtures:
            with self.subTest(fixture=name):
                self.assertEqual((embedded / 'fixtures' / name).read_bytes(), (CONTRACT / 'fixtures' / name).read_bytes())
        self.assertEqual((embedded / 'demo.png').read_bytes(), (CONTRACT / 'sample-panels/source/demo.png').read_bytes())


if __name__ == '__main__':
    unittest.main()
