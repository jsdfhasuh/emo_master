"""Real supplied SDK/export fixtures; no Runtime implementation is assumed."""
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from emo_panel_protocol.canonical import revisionOf, canonicalJson
from emo_web_panel.packages import PackageError, PackageStore

CONTRACT = Path(__file__).resolve().parents[1] / 'contracts' / 'panel-contract-v1'


def official_bundle(change=None, *, revise=True):
    with zipfile.ZipFile(CONTRACT / 'sample-panels' / 'demo.emoweb') as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    manifest = json.loads(files['manifest.json'])
    presentation = json.loads(files['presentation.json'])
    if change:
        change(manifest, presentation, files)
    if revise:
        unsigned = {k: v for k, v in manifest.items() if k != 'packageRevision'}
        manifest['packageRevision'] = revisionOf({'manifest': unsigned, 'presentation': presentation})
    files['manifest.json'] = canonicalJson(manifest).encode()
    files['presentation.json'] = canonicalJson(presentation).encode()
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as archive:
        for name, raw in files.items():
            archive.writestr(name, raw)
    return out.getvalue()


class OfficialPackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = PackageStore(Path(self.temp.name))

    def test_exact_export_and_sdk_revision(self):
        raw = (CONTRACT / 'sample-panels' / 'demo.emoweb').read_bytes()
        item = self.store.install(raw)
        self.assertEqual(item['validationStatus'], 'panel-contract-1.0')
        self.assertEqual(item['packageId'], hashlib.sha256(raw).hexdigest())
        self.assertEqual(item['presentation']['defaultPageId'], 'live')
        self.assertEqual([p['id'] for p in item['presentation']['pages']], ['live', 'history'])
        self.assertEqual(item['presentation']['historyDefinitionRevision'], item['manifest']['historyDefinitionRevision'])
        self.assertEqual(item['presentation']['dataSources'], item['officialPresentation']['dataSources'])
        self.assertEqual(self.store.asset(item['packageId'], 'demo')[1], 'image/png')
        self.store.activate(item['packageId'])
        self.assertEqual(self.store.active(), item)

    def test_revision_tampering_and_rollback(self):
        first = self.store.install(official_bundle())
        self.store.activate(first['packageId'])
        with self.assertRaisesRegex(PackageError, 'revision'):
            self.store.install(official_bundle(lambda m,p,f: p['pages']['live'].update(name='changed'), revise=False))
        second = self.store.install(official_bundle(lambda m,p,f: p['pages']['live'].update(name='changed')))
        self.store.activate(second['packageId'])
        self.store.activate(first['packageId'])
        self.assertEqual(self.store.active(), first)

    def test_renderer_requirements_fail_closed(self):
        with self.assertRaisesRegex(PackageError, 'renderer'):
            self.store.install(official_bundle(lambda m,p,f: m['rendererRequirements'].append('execute.python-v1')))

    def test_asset_byte_count_digest_filename_and_pixels(self):
        for key, value in [('byteSize', 1), ('sha256', '0'*64), ('path', 'assets/arbitrary.png')]:
            with self.subTest(key=key), self.assertRaises(PackageError):
                self.store.install(official_bundle(lambda m,p,f: m['assets']['demo'].update({key:value})))
        # A small compressed valid PNG whose dimensions exceed the contract.
        from PIL import Image
        out = io.BytesIO()
        Image.new('RGB', (2001, 2000)).save(out, format='PNG')
        data = out.getvalue(); digest = hashlib.sha256(data).hexdigest()
        def oversized(m,p,files):
            files.pop(m['assets']['demo']['path'])
            path = 'assets/' + digest + '.png'
            m['assets']['demo'].update(path=path,sha256=digest,byteSize=len(data))
            files[path] = data
        with self.assertRaisesRegex(PackageError, 'pixel'):
            self.store.install(official_bundle(oversized))

    def test_page_and_binding_references(self):
        changes = [lambda m,p,f: p.update(defaultPageId='missing'),
                   lambda m,p,f: p.update(pageOrder=['live','live']),
                   lambda m,p,f: p['pages']['live']['components'][0].update(bindings={'value':'missing'}),
                   lambda m,p,f: p['pages']['live']['components'][0].update(componentId=p['pages']['live']['components'][1]['componentId'])]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(PackageError):
                self.store.install(official_bundle(change))

    def test_no_runtime_paths_or_unknown_props(self):
        for props in [{'sqlite':{'databasePath':'/private/data.sqlite'}}, {'html':'<script/>'}]:
            with self.subTest(props=props), self.assertRaises(PackageError):
                self.store.install(official_bundle(lambda m,p,f: p['pages']['live']['components'][0]['props'].update(props)))

    def test_capture_requirements_cover_published_sources(self):
        with self.assertRaisesRegex(PackageError, 'Capture'):
            self.store.install(official_bundle(lambda m,p,f: m['captureRequirements'].update(sources=[])))

    def test_canonical_vectors_authoritative_sdk(self):
        vectors = json.loads((CONTRACT / 'fixtures' / 'canonical-vectors.json').read_text())
        self.assertTrue(vectors)
        # Vector wrapper/key details belong to the supplied contract, not JS.
        for vector in vectors.get('vectors', vectors) if isinstance(vectors, dict) else vectors:
            value = vector['input']
            self.assertEqual(canonicalJson(value), vector['canonicalUtf8'])
            self.assertEqual(revisionOf(value), vector['sha256'])


if __name__ == '__main__':
    unittest.main()
