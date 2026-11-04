"""Real supplied SDK/export fixtures; no Runtime implementation is assumed."""
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from emo_panel_protocol.canonical import revisionOf, canonicalJson
from emo_web_panel.packages import PackageError, PackageStore, _io_path

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
        self.store.activate(item['packageId'], runtime_id='configured')
        self.assertEqual(self.store.active('configured'), item)

    @unittest.skipUnless(os.name == 'nt', 'Windows MAX_PATH regression')
    def test_windows_long_package_asset_uses_extended_io_path(self):
        # Staging is short enough to create the file; renaming to the immutable
        # 64-character package ID makes its ordinary asset path exceed MAX_PATH.
        parent = Path(self.temp.name).resolve()
        long_root = parent / ('packages-' + 'x' * max(1, 140 - len(str(parent)) - 10))
        self.assertTrue(long_root.resolve().is_relative_to(parent))
        self.addCleanup(lambda: shutil.rmtree(_io_path(long_root)) if _io_path(long_root).exists() else None)
        store = PackageStore(long_root)
        item = store.install((CONTRACT / 'sample-panels' / 'demo.emoweb').read_bytes())
        asset = item['manifest']['assets']['demo']
        ordinary_path = store.root / item['packageId'] / asset['path']
        self.assertGreater(len(str(ordinary_path)), 260)
        data, mime = store.asset(item['packageId'], 'demo')
        self.assertEqual(mime, asset['mimeType'])
        self.assertEqual(hashlib.sha256(data).hexdigest(), asset['sha256'])

    @unittest.skipUnless(os.name == 'nt', 'Windows deep Web data directory regression')
    def test_windows_deep_editor_packages_install_and_reopen(self):
        # Same repository/data directory shape as the real browser failure:
        # atomic installation and a later open must both handle deep assets.
        parent = CONTRACT.parents[1] / 'test-results' / 'independent-editor'
        parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='package-install-', dir=_io_path(parent)) as temporary:
            data_root = Path(str(temporary).removeprefix('\\\\?\\')) / 'web'
            # Keep the MAX_PATH precondition true in short CI/worktree paths too.
            directory = data_root / ('packages-' + 'x' * max(1, 160 - len(str(data_root)) - 10))
            store = PackageStore(directory)
            for title in ('检测页', '检测页布局修改', '详情页发布'):
                def change(manifest, presentation, files):
                    presentation['pages']['live']['name'] = title
                item = store.install(official_bundle(change))
                ordinary = directory / item['packageId'] / item['manifest']['assets']['demo']['path']
                self.assertGreater(len(str(ordinary)), 260)
                data, mime = store.asset(item['packageId'], 'demo')
                self.assertEqual(mime, 'image/png')
                self.assertEqual(hashlib.sha256(data).hexdigest(), item['manifest']['assets']['demo']['sha256'])
                self.assertEqual(store.install(official_bundle(change))['packageId'], item['packageId'])

    @unittest.skipUnless(os.name == 'nt', 'Windows local rename sharing window')
    def test_windows_local_rename_retries_once_and_preserves_failure_limit(self):
        original = Path.rename
        calls = []
        def transient(path, target):
            calls.append((path, target))
            if len(calls) == 1:
                error = PermissionError('Temporary Windows rename access denial')
                error.winerror = 5
                raise error
            return original(path, target)
        with patch.object(Path, 'rename', transient), patch('emo_web_panel.packages.time.sleep') as delay:
            item = self.store.install(official_bundle())
        self.assertEqual(len(calls), 2)
        delay.assert_called_once_with(.1)
        self.assertEqual(self.store.get(item['packageId'])['packageId'], item['packageId'])
        denied = PermissionError('Still denied')
        denied.winerror = 5
        with patch.object(Path, 'rename', side_effect=denied) as rename, patch('emo_web_panel.packages.time.sleep'):
            with self.assertRaises(PermissionError):
                self.store.install(official_bundle(lambda m, p, f: p['pages']['live'].update(name='new release')))
        self.assertEqual(rename.call_count, 2)

    def test_delivered_spacer_demo_preserves_both_modes_without_capture_sources(self):
        item = self.store.install((CONTRACT / 'sample-panels' / 'demo.emoweb').read_bytes())
        def walk(components):
            for component in components:
                yield component
                yield from walk(component.get('children', []))
        spacers = [component for page in item['presentation']['pages']
                   for component in walk(page['components']) if component['type'] == 'spacer']
        self.assertEqual({component['props'].get('spacerMode', 'fixed') for component in spacers},
                         {'fixed', 'flex'})
        for component in spacers:
            self.assertNotIn('binding', component)
            self.assertFalse(component.get('actions'))
            self.assertFalse(component.get('history'))

    def test_spacer_survives_official_install_and_normalization(self):
        def add_spacers(manifest, presentation, files):
            page = presentation['pages']['live']
            page['layout'] = {'type':'responsive','direction':'vertical'}
            page['components'].extend([
                {'componentId':'fixed-gap','type':'spacer'},
                {'componentId':'flex-gap','type':'spacer','props':{'spacerMode':'flex'},
                 'responsive':{'weight':2,'minWidth':24,'preferredWidth':24,
                               'minHeight':24,'preferredHeight':24}},
            ])
        item = self.store.install(official_bundle(add_spacers))
        page = item['presentation']['pages'][0]
        fixed, elastic = page['components'][-2:]
        self.assertEqual(fixed['id'], 'fixed-gap')
        self.assertEqual(fixed['type'], 'spacer')
        self.assertNotIn('binding', fixed)
        self.assertEqual(elastic['props']['spacerMode'], 'flex')
        self.assertEqual(elastic['responsive']['weight'], 2)

    def test_expansion_survives_official_install_and_normalization(self):
        def expand(manifest, presentation, files):
            manifest['rendererRequirements'].append('layout.expanded-canvas-v1')
            page = presentation['pages']['live']
            page['canvasTop'] = -120
            page['components'][0].setdefault('responsive', {})['canvasOffsetY'] = -120
        item = self.store.install(official_bundle(expand))
        page = item['presentation']['pages'][0]
        self.assertEqual(page['canvasTop'], -120)
        self.assertEqual(page['components'][0]['responsive']['canvasOffsetY'], -120)

    def test_expansion_rejects_invalid_placement_or_undeclared_requirement(self):
        for invalid in ('outside', 'nested', 'grid', 'missing-requirement', 'old'):
            def change(manifest, presentation, files):
                manifest['rendererRequirements'].append('layout.expanded-canvas-v1')
                page = presentation['pages']['live']
                page['canvasTop'] = -120
                title = page['components'][0]
                title.setdefault('responsive', {})['canvasOffsetY'] = -120
                if invalid == 'outside':
                    title['responsive']['canvasOffsetY'] = -121
                elif invalid == 'nested':
                    page['components'] = [{'componentId': 'nested-title', 'type': 'container', 'children': [title]}]
                elif invalid == 'grid':
                    page['layout'] = {'type': 'grid'}
                elif invalid == 'old':
                    manifest['sourcePresentationVersion'] = '1.1'
                else:
                    manifest['rendererRequirements'] = [r for r in manifest['rendererRequirements']
                                                        if r != 'layout.expanded-canvas-v1']
            with self.subTest(invalid=invalid), self.assertRaises(PackageError):
                self.store.install(official_bundle(change))

    def test_official_spacer_rejects_bindings_actions_and_children(self):
        for extra in [
            {'bindings':{'value':'score'}},
            {'actions':{'clicked':{'type':'navigate','pageId':'live'}}},
            {'children':[{'componentId':'inside-gap','type':'text'}]},
        ]:
            def add(manifest, presentation, files):
                presentation['pages']['live']['components'].append(
                    {'componentId':'gap','type':'spacer',**extra})
            with self.subTest(extra=extra), self.assertRaises(PackageError):
                self.store.install(official_bundle(add))

    def test_official_grid_spacer_cannot_request_elastic_occupancy(self):
        def add(manifest, presentation, files):
            page = presentation['pages']['live']
            page['layout'] = {'type':'grid','columns':12}
            page['components'].append({'componentId':'gap','type':'spacer',
                                       'props':{'spacerMode':'flex'}})
        with self.assertRaisesRegex(PackageError, 'responsive'):
            self.store.install(official_bundle(add))

    def test_revision_tampering_and_rollback(self):
        first = self.store.install(official_bundle())
        self.store.activate(first['packageId'], runtime_id='configured')
        with self.assertRaisesRegex(PackageError, 'revision'):
            self.store.install(official_bundle(lambda m,p,f: p['pages']['live'].update(name='changed'), revise=False))
        second = self.store.install(official_bundle(lambda m,p,f: p['pages']['live'].update(name='changed')))
        self.store.activate(second['packageId'], runtime_id='configured')
        self.store.activate(first['packageId'], runtime_id='configured')
        self.assertEqual(self.store.active('configured'), first)

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
        vectors = json.loads((CONTRACT / 'fixtures' / 'canonical-vectors.json').read_text(encoding='utf-8'))
        self.assertTrue(vectors)
        # Vector wrapper/key details belong to the supplied contract, not JS.
        for vector in vectors.get('vectors', vectors) if isinstance(vectors, dict) else vectors:
            value = vector['input']
            self.assertEqual(canonicalJson(value), vector['canonicalUtf8'])
            self.assertEqual(revisionOf(value), vector['sha256'])


if __name__ == '__main__':
    unittest.main()
