"""Pin all consumed handoff bytes, including exact SDK wheel, on both OSes."""
import hashlib
import json
from pathlib import Path
import unittest

CONTRACT=Path(__file__).resolve().parents[1]/'contracts/panel-contract-v1'

class ContractManifestTests(unittest.TestCase):
    def test_all_authoritative_files_match_clean_handoff(self):
        manifest=json.loads((CONTRACT/'contract-manifest.json').read_bytes())
        self.assertEqual(manifest['sourceSha'],'d4ab96409e43d7ab6ae566820da357508d01658c')
        self.assertFalse(manifest['sourceDirty'])
        self.assertEqual(manifest['sdkVersion'],'1.1.0')
        self.assertEqual(len(manifest['files']),34)
        for name,expected in manifest['files'].items():
            with self.subTest(file=name):
                data=(CONTRACT/name).read_bytes()
                self.assertEqual(len(data),expected['byteSize'])
                self.assertEqual(hashlib.sha256(data).hexdigest(),expected['sha256'])
        self.assertEqual(hashlib.sha256((CONTRACT/'python/emo_panel_protocol-1.1.0-py3-none-any.whl').read_bytes()).hexdigest(),
                         'bb0cff1f007b8c6b850c8e43d792200ee6adebbaa4af23c4a966362b74e697f9')
