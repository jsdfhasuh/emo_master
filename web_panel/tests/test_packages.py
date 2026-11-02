import hashlib
import io
import json
from pathlib import Path
import stat
import tempfile
import unittest
import warnings
import zipfile

from emo_web_panel.packages import PackageError, PackageStore, TYPES

PNG = b'\x89PNG\r\n\x1a\n' + b'safe-test-fixture'


def bundle(revision='1', components=None, changes=None, extras=None):
    manifest = {'format':'emo-web-panel-1', 'projectId':'demo-project',
                'packageRevision':revision, 'panelContractVersion':'draft-unverified',
                'rendererRequirements':['image','text'], 'presentationFile':'presentation.json',
                'assets':[{'id':'sample','path':'assets/sample.png','mimeType':'image/png',
                           'sha256':hashlib.sha256(PNG).hexdigest()}]}
    if changes:
        manifest.update(changes)
    presentation = {'pages':[{'id':'main','title':'测试', 'components': components or [
        {'id':'title','type':'text','props':{'text':'Hello'}},
        {'id':'photo','type':'image','props':{'assetId':'sample'}}]}]}
    data = {'manifest.json':json.dumps(manifest).encode(),
            'presentation.json':json.dumps(presentation).encode(), 'assets/sample.png':PNG}
    data.update(extras or {})
    out = io.BytesIO()
    with zipfile.ZipFile(out,'w') as z:
        for name, raw in data.items():
            z.writestr(name,raw)
    return out.getvalue()


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = PackageStore(Path(self.temp.name))

    def test_install_activate_rollback_and_asset(self):
        self.assertIsNone(self.store.active('configured'))
        first = self.store.install(bundle())
        second = self.store.install(bundle('2'))
        self.assertEqual(first['validationStatus'],'draft-schema-only')
        self.assertEqual(self.store.install(bundle()),first)
        self.assertEqual(len(self.store.list()),2)
        self.store.activate(first['packageId'], runtime_id='configured')
        self.store.activate(second['packageId'], runtime_id='configured')
        self.store.activate(first['packageId'], runtime_id='configured')
        self.assertEqual(self.store.active('configured'),first)
        self.assertEqual(self.store.asset(first['packageId'],'sample'),(PNG,'image/png'))

    def test_official_or_unknown_contract_not_misread_as_draft(self):
        for version in ['1','999','draft-1']:
            with self.assertRaises(PackageError):self.store.install(bundle(changes={'panelContractVersion':version}))
    def test_same_revision_different_bytes_rejected(self):
        self.store.install(bundle())
        with self.assertRaises(PackageError):
            self.store.install(bundle(components=[{'id':'other','type':'text','props':{}}]))
        self.assertEqual(len(self.store.list()),1)

    def test_actual_demo_fixture_installs_all_thirteen_types(self):
        from emo_web_panel.mock_runtime import demo_panel
        demo=demo_panel()
        installed=self.store.install(bundle(extras={
            'presentation.json':json.dumps({'pages':demo['pages']}).encode()}))
        types={c['type'] for c in installed['presentation']['pages'][0]['components']}
        self.assertEqual(types,TYPES)

    def test_aggregate_is_allowlisted(self):
        for value in ['count','sum','avg','min','max']:
            self.store.install(bundle(value,components=[{'id':'x','type':'sqlite_value','props':{'aggregate':value}}]))
        for value in ['SELECT *', {}, [], 0]:
            with self.subTest(value=value),self.assertRaises(PackageError):
                self.store.install(bundle('invalid',components=[{'id':'x','type':'sqlite_value','props':{'aggregate':value}}]))

    def test_all_component_types_and_nested_container(self):
        comps = [{'id':t,'type':t,'label':t,'props':{},'binding':'sample'} for t in sorted(TYPES)]
        comps[-1]['grid'] = {'column':1,'row':1,'columnSpan':2,'rowSpan':1}
        self.store.install(bundle(components=comps))
        self.store.install(bundle('2',components=[{'id':'box','type':'container','props':{},
            'children':[{'id':'child','type':'number','binding':{'sourceId':'number'},'props':{}}]}]))

    def test_unsafe_paths(self):
        for name in ['../evil', '/tmp/evil', 'C:/evil', 'assets\\evil', 'assets/../evil', 'assets//evil', 'assets/CON', 'assets/evil.']:
            with self.subTest(name=name), self.assertRaises(PackageError):
                self.store.install(bundle(extras={name:b'x'}))
        self.assertEqual(self.store.list(),[])

    def test_duplicate_archive_and_symlink(self):
        for symlink in [False,True]:
            out = io.BytesIO(bundle())
            with zipfile.ZipFile(out,'a') as z:
                if symlink:
                    info = zipfile.ZipInfo('assets/link')
                    info.create_system = 3
                    info.external_attr = (stat.S_IFLNK | 0o777) << 16
                    z.writestr(info,'../../secret')
                else:
                    with warnings.catch_warnings():
                        warnings.simplefilter('ignore')
                        z.writestr('manifest.json','{}')
            with self.assertRaises(PackageError):
                self.store.install(out.getvalue())

    def test_size_count_ratio_limits(self):
        store = self.store
        store.MAX_ARCHIVE = 4
        with self.assertRaises(PackageError): store.install(bundle())
        store.MAX_ARCHIVE = 16*1024*1024
        store.MAX_ENTRIES = 2
        with self.assertRaises(PackageError): store.install(bundle())
        store.MAX_ENTRIES = 256
        store.MAX_FILE = 5
        with self.assertRaises(PackageError): store.install(bundle())
        store.MAX_FILE = 8*1024*1024
        store.MAX_EXPANDED = 10
        with self.assertRaises(PackageError): store.install(bundle())
        store.MAX_EXPANDED = 32*1024*1024
        out = io.BytesIO()
        with zipfile.ZipFile(out,'w',compression=zipfile.ZIP_DEFLATED) as z:
            z.writestr('bomb','0'*100000)
        with self.assertRaises(PackageError): store.install(out.getvalue())

    def test_assets_validate_declared_type_digest_and_magic(self):
        for change in [ {'mimeType':'image/svg+xml'}, {'sha256':'0'*64}, {'path':'../p.png'} ]:
            asset={'id':'sample','path':'assets/sample.png','mimeType':'image/png',
                   'sha256':hashlib.sha256(PNG).hexdigest(), **change}
            with self.subTest(change=change),self.assertRaises(PackageError):
                self.store.install(bundle(changes={'assets':[asset]}))
        with self.assertRaises(PackageError):
            self.store.install(bundle(extras={'assets/sample.png':b'<script>alert(1)</script>'}))

    def test_unknown_files_schema_scripts_and_navigation(self):
        with self.assertRaises(PackageError): self.store.install(bundle(extras={'evil.py':b'pass'}))
        cases=[{'html':'<script>x</script>'}, {'style':{'backgroundImage':'url(https://evil)'}},
               {'style':{'color':'url(javascript:x)'}}, {'action':'exec'}, {'targetPageId':'missing'},
               {'assetId':'missing'}]
        for props in cases:
            with self.subTest(props=props),self.assertRaises(PackageError):
                self.store.install(bundle(components=[{'id':'bad','type':'text','props':props}]))

    def test_duplicate_component_and_unknown_type(self):
        for comps in [[{'id':'x','type':'evil','props':{}}],
                      [{'id':'x','type':'text'},{'id':'x','type':'text'}]]:
            with self.assertRaises(PackageError): self.store.install(bundle(components=comps))

    def test_corrupt_json_duplicate_keys_and_nonfinite(self):
        for raw in [b'{', b'{"a":1,"a":2}',b'{"x":NaN}']:
            with self.assertRaises(PackageError):
                self.store.install(bundle(extras={'manifest.json':raw}))

    def test_malformed_types_are_validation_errors(self):
        for change in [{'rendererRequirements':[{}]}, {'projectId':[]}, {'assets':{}}, {'format':[]}]:
            with self.subTest(change=change),self.assertRaises(PackageError):
                self.store.install(bundle(changes=change))
        for props in [{'style':{'fontWeight':[]}}, {'action':{}}, {'assetId':[]}]:
            with self.subTest(props=props),self.assertRaises(PackageError):
                self.store.install(bundle(components=[{'id':'x','type':'text','props':props}]))

    def test_unknown_ids_and_corrupt_stored_asset(self):
        with self.assertRaises(PackageError): self.store.get('../outside')
        p=self.store.install(bundle())
        with self.assertRaises(PackageError): self.store.asset(p['packageId'],'unknown')
        asset=Path(self.temp.name)/p['packageId']/'assets/sample.png'
        asset.write_bytes(b'changed')
        with self.assertRaises(PackageError): self.store.asset(p['packageId'],'sample')

    def test_grid_and_mobile_require_supported_schema(self):
        for grid in [{'column': '1; background:red'}, {'column': -1}, {'evil': 1}]:
            with self.subTest(grid=grid), self.assertRaises(PackageError):
                self.store.install(bundle(components=[{'id':'x','type':'text','grid':grid}]))
        with self.assertRaises(PackageError):
            self.store.install(bundle(extras={'presentation.json':json.dumps({'pages':[
                {'id':'main','mobile':{},'components':[]}]}).encode()}))

    def test_per_type_property_matrix(self):
        cases = [('text', {'action':'freeze'}), ('image', {'aggregate':'count'}),
                 ('clock', {'assetId':'sample'}), ('number', {'columns':[]}),
                 ('sqlite_table', {'sourceMode':'history_fixed'}),
                 ('page_title', {'mapping':{}}), ('navigation_button', {'timezone':'UTC'})]
        for kind, props in cases:
            with self.subTest(kind=kind), self.assertRaises(PackageError):
                self.store.install(bundle(components=[{'id':'x','type':kind,'props':props}]))

    def test_history_selection_requires_real_table(self):
        image = {'id':'image', 'type':'image', 'props':{'sourceMode':'history_selection','sourceComponentId':'history'}}
        for target in (None, {'id':'history','type':'table'}, {'id':'history','type':'image'}):
            with self.subTest(target=target), self.assertRaises(PackageError):
                self.store.install(bundle(components=[image] + ([target] if target else [])))
        self.store.install(bundle(components=[image, {'id':'box','type':'container','children':[
            {'id':'history','type':'sqlite_table'}]}]))

    def test_image_modes_are_exclusive(self):
        for props in ({'sourceMode':'script'}, {'sourceMode':[]},
                      {'sourceMode':'history_selection'}, {'sourceComponentId':'history'},
                      {'sourceMode':'history_fixed','sourceComponentId':'history'},
                      {'sourceMode':'history_fixed','assetId':'sample'}):
            with self.subTest(props=props), self.assertRaises(PackageError):
                self.store.install(bundle(components=[{'id':'x','type':'image','props':props}]))
        with self.assertRaises(PackageError):
            self.store.install(bundle(components=[{'id':'x','type':'image','binding':'live',
                'props':{'sourceMode':'history_fixed'}}]))
        self.store.install(bundle(components=[{'id':'x','type':'image','props':{'sourceMode':'history_fixed'}}]))

    def test_clock_format_timezone(self):
        for props in ({'format':'%n<script>'}, {'format':[]}, {'timezone':'../etc/passwd'},
                      {'timezone':'Not/A_Zone'}, {'timezone':[]}, {'timezone':'https://zone'}):
            with self.subTest(props=props), self.assertRaises(PackageError):
                self.store.install(bundle(components=[{'id':'x','type':'clock','props':props}]))
        for index, zone in enumerate(('UTC','browser')):
            self.store.install(bundle(str(index), components=[{'id':'x','type':'clock',
                'props':{'format':'datetime','timezone':zone}}]))

    def test_typed_renderer_properties(self):
        for kind, props in [('number', {'decimals':True}), ('number',{'decimals':9}),
                            ('indicator',{'mapping':[]}), ('text',{'text':{}}),
                            ('table',{'columns':['name']}),
                            ('table',{'columns':[{'key':'x','path':'/tmp'}]}),
                            ('table',{'columns':[{'key':'x'},{'key':'x'}]})]:
            with self.subTest(kind=kind,props=props), self.assertRaises(PackageError):
                self.store.install(bundle(components=[{'id':'x','type':kind,'props':props}]))

    def test_depth_and_children_rejected(self):
        child={'id':'deep','type':'text','props':{}}
        for i in range(10): child={'id':f'container{i}','type':'container','props':{},'children':[child]}
        with self.assertRaises(PackageError): self.store.install(bundle(components=[child]))
        with self.assertRaises(PackageError):
            self.store.install(bundle(components=[{'id':'t','type':'text','children':[{'id':'c','type':'text'}]}]))


if __name__ == '__main__': unittest.main()
