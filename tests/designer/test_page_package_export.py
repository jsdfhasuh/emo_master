"""Current responsive drafts use the managed package and its existing installer."""
import json
import zipfile

import pytest
from PySide2.QtWidgets import QFileDialog, QMessageBox

from emo_master.apps.designer.page_designer.delivery import exportTestPackage
from emo_master.apps.designer.state.project_store import saveProject
from emo_master.core.project.delivery_store import DeliveryStore
from emo_master.core.project.package_builder import buildPageTestPackage
from emo_master.core.project.runtime_package import FORMAT, installRuntimePackage
from tests.designer.test_node_context_menu import Client


def testResponsiveDraftExportPreservesPagesDevicesAndSavedProject(ownedDesignerWindow, tmp_path):
    client = Client()
    window = ownedDesignerWindow(client)
    root = tmp_path / 'engineering'
    assert window.saveProjectToDirectory(str(root))
    disk = (root / 'project.json').read_bytes()
    coordinator = window.pageCoordinator
    coordinator.showPages()
    editor = coordinator.editor
    editor.pageId = editor.store.createPage('Responsive export')
    key = editor.tools.commands().add(editor.pageId, 'text', 0, 0)
    coordinator.session.workflows.devices['definitions']['simulated'] = dict(
        name='Simulated only', deviceTypeId='device.simulated.camera', driverVersion='1.0.0',
        connection={'serial': 'TEST-ONLY'}, defaults={'width': 7, 'height': 5, 'exposureUs': 8000, 'gain': 0})
    editor.refresh()
    editor.tools.select(key)
    editor.tools.fields['text'].setText('Unapplied draft exported')
    package = exportTestPackage(coordinator, tmp_path / 'packages')
    expected = coordinator.session.document()
    assert coordinator.session.dirty
    assert (root / 'project.json').read_bytes() == disk
    assert expected.presentation.pages[editor.pageId].components[0].props.text == 'Unapplied draft exported'
    with zipfile.ZipFile(package) as archive:
        manifest = json.loads(archive.read('manifest.json'))
        payload = json.loads(archive.read('project.json'))
    assert manifest['format'] == FORMAT and manifest['projectSchema'] == '2.8'
    assert payload['presentation']['schemaVersion'] == '1.1'
    assert payload['devices']['schemaVersion'] == '1.0'
    assert 'device.simulated.camera' in manifest['requiredDeviceTypes']
    installed = installRuntimePackage(package, tmp_path / 'installed')
    assert installed.presentation == expected.presentation
    assert installed.devices == expected.devices
    assert installed.workflows == expected.workflows
    assert client.starts == client.stops == 0
    before, depth = coordinator.session.payload(), len(coordinator.session._undo)
    exportTestPackage(coordinator, tmp_path / 'packages')
    assert coordinator.session.payload() == before and len(coordinator.session._undo) == depth
    with pytest.raises(ValueError, match='managed Runtime'):
        buildPageTestPackage(expected, root, tmp_path / 'legacy', registry={})


def testResponsiveExportMenuUsesRuntimePackageAndExistingImportRoute(ownedDesignerWindow, tmp_path, monkeypatch):
    window = ownedDesignerWindow(Client())
    assert window.saveProjectToDirectory(str(tmp_path / 'engineering'))
    coordinator = window.pageCoordinator
    action = next(action for action in coordinator.chrome.menus['文件'].actions()
                  if action.text().startswith('导出'))
    assert action.text() == '导出 Runtime 项目包…'
    messages, errors = [], []
    monkeypatch.setattr(QFileDialog, 'getExistingDirectory', lambda *_args: str(tmp_path / 'packages'))
    monkeypatch.setattr(QMessageBox, 'information', lambda *args: messages.append(args))
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: errors.append(args))
    action.trigger()
    assert not errors and len(messages) == 1
    assert '更新工程' in messages[0][-1]
    assert len(list((tmp_path / 'packages').glob('*.vxpkg'))) == 1


def testExportRequiresSavedResourceRoot(ownedDesignerWindow, tmp_path):
    window = ownedDesignerWindow(Client())
    with pytest.raises(ValueError, match='先保存项目'):
        exportTestPackage(window.pageCoordinator, tmp_path / 'packages')
    assert not (tmp_path / 'packages').exists()


def testLegacyPageTestPackageKeepsItsExistingRestrictedImporter(ownedDesignerWindow, tmp_path):
    from examples.runtime_pages_p2 import sampleProject

    # Explicit compatibility fixture: this is the old 2.2 test-delivery route.
    root = tmp_path / 'legacy'
    root.mkdir()
    document = sampleProject(root)
    saveProject(root, document.model_dump(mode='json'))
    window = ownedDesignerWindow(Client())
    assert window.loadProjectDirectory(str(root))
    coordinator = window.pageCoordinator
    assert coordinator.chrome.exportAction.text() == '导出测试项目包…'
    package = exportTestPackage(coordinator, tmp_path / 'packages')
    delivery = DeliveryStore(tmp_path / 'legacy-installed')
    revision = delivery.importPackage(package)
    installed, _manifest = delivery.verify(revision)
    assert installed.schemaVersion == '2.2'
    assert installed.presentation == document.presentation
    assert delivery.state()['active'] is None
