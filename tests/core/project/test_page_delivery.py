import json
import zipfile

import pytest

from emo_master.core.project.package_builder import buildPageTestPackage, buildPackage
from emo_master.core.project.test_delivery import packagePath, trustedRegistry
from examples.runtime_pages_p2 import sampleProject


@pytest.fixture(scope='module')
def registry():
    return trustedRegistry()


def testExportFrozenAllowlistAndLegacyGuard(tmp_path, registry):
    root = tmp_path/'project'
    root.mkdir()
    doc = sampleProject(root)
    (root/'project.json').write_text(doc.model_dump_json(), encoding='utf-8')
    for name in ['runtime.sqlite3', 'secret.txt', 'old.vxpkg', 'run.log', 'evidence.json', 'cache.bin']:
        (root/name).write_text('private')
    doc.workflows['main'].nodes[1].params['imagePath'] = 'D:/developer/secret/input.png'
    package = buildPageTestPackage(doc, root, tmp_path/'packages', registry)
    original = package.read_bytes()
    with zipfile.ZipFile(package) as z:
        assert set(z.namelist()) == {'manifest.json', 'project.json', 'input.png'}
        project = json.loads(z.read('project.json'))
        assert project['workflows']['main']['nodes'][1]['params']['imagePath'] == ''
        manifest = json.loads(z.read('manifest.json'))
        assert manifest['compatibility']['operators']['vision.collection.count'] == '1.1.0'
    doc.presentation.pages['main'].name = 'changed after freeze'
    (root/'input.png').write_bytes(b'changed')
    assert package.read_bytes() == original
    with pytest.raises(ValueError, match='P5'):
        buildPackage(root, tmp_path/'legacy')
    with pytest.raises(ValueError, match='outside'):
        buildPageTestPackage(doc, root, root/'output', registry)


@pytest.mark.parametrize('name', ['../x', '/a', 'C:/x', 'a\\b', 'a//b', 'NUL.png',
    'con.foo', 'a./b', 'a /b', 'a:stream', 'COM¹.jpg', 'a~1.png', 'a/../b', 'NUL .png', 'CONOUT$.png'])
def testPortablePathRejectsAliases(name):
    with pytest.raises(ValueError):
        packagePath(name)


def testUnsupportedCapabilitiesAndMissingResourcesRefuseExport(tmp_path, registry):
    root = tmp_path/'project'
    root.mkdir()
    doc = sampleProject(root)
    doc.presentation.pages['main'].components[0].bindings.clear()
    with pytest.raises(ValueError, match='unbound'):
        buildPageTestPackage(doc, root, tmp_path/'out', registry)
    doc = sampleProject(root)
    doc.workflows['main'].nodes[1].operatorId = 'vision.io.huaray_camera'
    with pytest.raises(ValueError, match='unsupported test operator'):
        buildPageTestPackage(doc, root, tmp_path/'out', registry)
    doc = sampleProject(root)
    (root/'input.png').unlink()
    with pytest.raises(ValueError, match='missing'):
        buildPageTestPackage(doc, root, tmp_path/'out', registry)


def testStaticLabelsAndPlatformStatusAreNotUnboundBusinessData(tmp_path, registry):
    from emo_master.core.presentation.models import Component
    doc = sampleProject(tmp_path)
    doc.presentation.pages['main'].components.extend([
        Component(componentId='label', type='text', props={'text': 'Test only'}, layout={'row': 2}),
        Component(componentId='status', type='runtime_status', layout={'row': 3})])
    assert buildPageTestPackage(doc, tmp_path, tmp_path.parent/'static-packages', registry).exists()
    doc.presentation.pages['main'].components[-2].props.text = ''
    with pytest.raises(ValueError, match='unbound'):
        buildPageTestPackage(doc, tmp_path, tmp_path.parent/'static-packages', registry)


def testDeviceMetadataAndUnknownDependenciesAreExplicitlyRejected(tmp_path, registry):
    doc = sampleProject(tmp_path)
    doc.devices.bindings['camera'] = {'ip': 'not-a-real-device'}
    with pytest.raises(ValueError, match='device configuration'):
        buildPageTestPackage(doc, tmp_path, tmp_path.parent/'packages', registry)
    doc.devices.bindings.clear()
    doc.dependencies.operators = ['unknown.plugin']
    with pytest.raises(ValueError, match='dependency'):
        buildPageTestPackage(doc, tmp_path, tmp_path.parent/'packages', registry)


def testSourceReplacementDuringExportNeverPublishesPartialPackage(tmp_path, registry, monkeypatch):
    from emo_master.core.project import test_delivery
    root = tmp_path/'draft'
    root.mkdir()
    document = sampleProject(root)
    validate = test_delivery.validateTestProject
    def replaced(*args):
        compatibility = validate(*args)
        (root/'input.png').write_bytes(b'changed during export')
        return compatibility
    monkeypatch.setattr(test_delivery, 'validateTestProject', replaced)
    with pytest.raises(ValueError, match='changed during export'):
        buildPageTestPackage(document, root, tmp_path/'out', registry)
    assert not list((tmp_path/'out').iterdir())
