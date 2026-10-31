"""Pin complete official SDK 1.1.1 delivery and embedded consumer bytes on both OSes."""
import hashlib
import json
from pathlib import Path
import unittest
import zipfile

PANEL = Path(__file__).resolve().parents[1]
CONTRACT = PANEL / 'contracts/panel-contract-v1'
HANDOFF = PANEL.parent / 'docs/handoffs/runtime-web-panel-v1/sdk-1.1.1'
SOURCE_SHA = '95c6371542f73a725c0ca2fd585152c728338dbf'
ZIP_SHA = '8f0efaf9b918d44684c5c8c8b3f0d67fee740179ea33d17c2a064c801d9d3b7a'
WHEEL_SHA = 'aed06e1badb4f3fedcca121413391dbf88168cc02dc37fc54f65cbf089c92858'
WHEEL_PATH = 'python/emo_panel_protocol-1.1.1-py3-none-any.whl'


class ContractManifestTests(unittest.TestCase):
    def test_all_authoritative_files_match_clean_handoff(self):
        manifest = json.loads((CONTRACT / 'contract-manifest.json').read_bytes())
        self.assertEqual(manifest['sourceSha'], SOURCE_SHA)
        self.assertFalse(manifest['sourceDirty'])
        self.assertEqual(manifest['sdkVersion'], '1.1.1')
        self.assertEqual(manifest['panelContractVersion'], '1.0')
        self.assertEqual(manifest['sdkWheels'], [WHEEL_PATH])
        self.assertEqual(len(manifest['files']), 39)
        expected_files = set(manifest['files']) | {'contract-manifest.json'}
        self.assertEqual({p.relative_to(CONTRACT).as_posix() for p in CONTRACT.rglob('*') if p.is_file()}, expected_files)
        for name, expected in manifest['files'].items():
            with self.subTest(file=name):
                data = (CONTRACT / name).read_bytes()
                self.assertEqual(len(data), expected['byteSize'])
                self.assertEqual(hashlib.sha256(data).hexdigest(), expected['sha256'])
        wheel = (CONTRACT / WHEEL_PATH).read_bytes()
        self.assertEqual(len(wheel), 38038)
        self.assertEqual(hashlib.sha256(wheel).hexdigest(), WHEEL_SHA)

    def test_vendored_tree_is_exact_official_zip_not_regenerated(self):
        archive_path = HANDOFF / 'panel-contract-v1.zip'
        raw = archive_path.read_bytes()
        self.assertEqual(len(raw), 90424)
        self.assertEqual(hashlib.sha256(raw).hexdigest(), ZIP_SHA)
        with zipfile.ZipFile(archive_path) as archive:
            self.assertIsNone(archive.testzip())
            self.assertEqual(len(archive.namelist()), 40)
            for name in archive.namelist():
                with self.subTest(file=name):
                    self.assertEqual((CONTRACT / name).read_bytes(), archive.read(name))
        self.assertEqual((CONTRACT / WHEEL_PATH).read_bytes(), (HANDOFF / Path(WHEEL_PATH).name).read_bytes())
        # Outer tracked manifest may have LF normalization. The ZIP manifest is
        # the byte authority; the separately committed copy must agree as JSON.
        self.assertEqual(json.loads((HANDOFF / 'contract-manifest.json').read_bytes()),
                         json.loads((CONTRACT / 'contract-manifest.json').read_bytes()))

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
