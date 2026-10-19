"""Device creation stays editable until validation and explicit reuse succeed."""
from concurrent.futures import Future
from copy import deepcopy

import pytest
from PySide2.QtWidgets import QApplication, QMessageBox, QTabWidget, QWidget

from emo_master.apps.designer.ui import device_wizard as module
from tests.designer.test_device_identity_reuse import builtin
from tests.designer.test_device_workspace import windowFor, waitUntil
from tests.core.project.test_devices import declaration, definition  # noqa: F401


def testPreacceptRejectionRetainsCandidateUntilCorrected(retainedQtApplication):
    calls = []

    def commit(candidate):
        calls.append(deepcopy(candidate))
        if len(calls) == 1:
            raise ValueError('已有设备使用相同标识')
        return True

    dialog = module.DeviceDefinitionDialog(builtin('simulated'), beforeAccept=commit)
    dialog.show()
    try:
        dialog.name.setText('保留名称')
        dialog.forms['defaults']._controls['gain'].setText('7.5')
        dialog.accept()
        assert dialog.isVisible() and dialog.result() != dialog.Accepted
        assert dialog.value is None and '相同标识' in dialog.error.text()
        assert dialog.name.text() == '保留名称'
        assert dialog.forms['defaults'].getValues()['gain'] == 7.5
        dialog.forms['connection']._controls['serial'].setText('UNIQUE')
        dialog.accept()
        assert dialog.result() == dialog.Accepted and not dialog.isVisible()
        assert len(calls) == 2
        assert dialog.value['connection']['serial'] == 'UNIQUE'
        assert dialog.value['defaults']['gain'] == 7.5
    finally:
        dialog.reject()


def testPreacceptRefusalKeepsFormAndPendingSetupCannotCommit(retainedQtApplication):
    calls = []
    dialog = module.DeviceDefinitionDialog(builtin('simulated'), beforeAccept=lambda value: calls.append(value) or False)
    dialog.show()
    try:
        pending = Future()
        dialog.setupPending = pending
        dialog.accept()
        assert calls == [] and dialog.value is None and dialog.isVisible()
        assert '等待' in dialog.error.text()
        dialog.setupPending = None
        dialog.accept()
        assert len(calls) == 1 and dialog.value is None and dialog.isVisible()
        assert dialog.result() != dialog.Accepted
    finally:
        dialog.setupPending = None
        dialog.reject()


@pytest.mark.parametrize('text,message', [('', '设备名称不能为空'), ('   ', '设备名称不能为空'), ('名' * 161, '160')])
def testDeviceNameErrorUsesChineseAndKeepsInput(retainedQtApplication, text, message):
    dialog = module.DeviceDefinitionDialog(builtin('simulated'))
    dialog.show()
    try:
        dialog.name.setText(text)
        dialog.accept()
        QApplication.processEvents()
        assert dialog.isVisible() and dialog.value is None
        assert message in dialog.error.text()
        assert dialog.name.text() == text
        assert dialog.name.hasFocus()
    finally:
        dialog.reject()


def testLocalFieldErrorShowsTabAndFocusWithoutResettingInput(retainedQtApplication):
    dialog = module.DeviceDefinitionDialog(builtin('simulated'))
    dialog.show()
    try:
        tabs = dialog.findChild(QTabWidget)
        tabs.setCurrentIndex(0)
        control = dialog.forms['defaults']._controls['gain']
        control.setText('-1')
        dialog.accept()
        QApplication.processEvents()
        assert dialog.isVisible() and dialog.value is None
        assert '增益' in dialog.error.text() and '不能小于 0' in dialog.error.text()
        assert tabs.currentIndex() == 1 and control.hasFocus()
        assert control.text() == '-1' and control.accessibleDescription()
        control.setText('0')
        dialog.accept()
        assert dialog.result() == dialog.Accepted
    finally:
        dialog.reject()


def testPolicyLabelsArePresentationOnly(retainedQtApplication):
    metadata = builtin('simulated')
    before = deepcopy(metadata.policiesSchema)
    form = module.configurationForm(metadata.policiesSchema, {'connect': 'on_project_open', 'release': 'when_unused'})
    try:
        for key in ('connect', 'release'):
            combo = form._controls[key]
            assert [combo.itemData(index) for index in range(combo.count())] == before['properties'][key]['enum']
            assert all(combo.itemText(index) != combo.itemData(index) for index in range(combo.count()))
        assert form.getValues()['connect'] == 'on_project_open'
        assert form.getValues()['release'] == 'when_unused'
        assert metadata.policiesSchema == before
    finally:
        form.close()


def testSharedValidationPreservesMetadataAndFindsOffsetTabs(retainedQtApplication):
    metadata = builtin('simulated')
    dialog = module.DeviceDefinitionDialog(metadata)
    dialog.show()
    try:
        tabs = dialog.findChild(QTabWidget)
        tabs.insertTab(0, QWidget(), '额外页面')
        base = dialog.definition()
        base.update(enabled=False, description='保留说明')
        before = deepcopy(base)
        validated = module.validateDeviceDefinition(metadata, dialog.name, dialog.forms, tabs, base=base)
        assert validated['enabled'] is False and validated['description'] == '保留说明'
        assert base == before
        dialog.forms['defaults']._controls['gain'].setText('-1')
        tabs.setCurrentIndex(0)
        with pytest.raises(ValueError, match='增益'):
            module.validateDeviceDefinition(metadata, dialog.name, dialog.forms, tabs, base=base)
        QApplication.processEvents()
        assert tabs.currentIndex() == 2
        assert dialog.forms['defaults']._controls['gain'].hasFocus()
    finally:
        dialog.reject()


def testPolicyCombinationErrorUsesChineseAndShowsPolicyTab(retainedQtApplication):
    dialog = module.DeviceDefinitionDialog(builtin('simulated'))
    dialog.show()
    try:
        form = dialog.forms['policies']
        form._controls['connect'].setCurrentIndex(form._controls['connect'].findData('manual'))
        form._controls['release'].setCurrentIndex(form._controls['release'].findData('when_unused'))
        dialog.accept()
        QApplication.processEvents()
        assert dialog.value is None and dialog.isVisible()
        assert '释放策略' in dialog.error.text() and '首次使用时连接' in dialog.error.text()
        assert dialog.tabs.currentIndex() == 2 and form._controls['release'].hasFocus()
        assert form._controls['release'].currentData() == 'when_unused'
        form._controls['connect'].setCurrentIndex(form._controls['connect'].findData('on_first_use'))
        dialog.accept()
        assert dialog.result() == dialog.Accepted
        assert dialog.value['policies']['release'] == 'when_unused'
    finally:
        dialog.reject()


def testSharedValidationStillUsesCoreIdentityGuard(retainedQtApplication):
    dialog = module.DeviceDefinitionDialog(builtin('simulated'))
    try:
        base = dialog.definition()
        base['driverVersion'] = '2.0.0'
        with pytest.raises(ValueError, match='device type/version does not match declaration'):
            module.validateDeviceDefinition(dialog.declaration, dialog.name, dialog.forms, base=base)
    finally:
        dialog.reject()


def testNodeReuseNoRetainsDialogForCorrectionAndCancelRemainsAtomic(ownedDesignerWindow, tmp_path,
        declaration, definition, monkeypatch):  # noqa: F811
    declaration.connectionSchema['properties']['source']['xDeviceIdentity'] = True
    window, requirements = windowFor(ownedDesignerWindow, tmp_path, definition, declaration)
    node = dict(nodeId='new', operatorId='vision.io.huaray_camera', params={}, paramSchema={})
    wizard = module.DeviceNodeDialog(window.pageCoordinator, node, requirements[node['operatorId']], window)
    candidate = definition.model_dump()
    candidate['defaults']['gain'] = 7.5
    opened = []

    def edit(dialog):
        opened.append(dialog)
        dialog.show()
        dialog.name.setText('继续编辑的设备')
        for key, form in dialog.forms.items():
            form.setSchema(module.configurationSchema(getattr(declaration, key + 'Schema')), candidate[key])
        dialog.accept()
        assert dialog.isVisible() and dialog.value is None
        assert not wizard.newDefinitions
        assert wizard.slots['camera'][0].currentData() is None
        assert dialog.name.text() == '继续编辑的设备'
        assert dialog.forms['defaults'].getValues()['gain'] == 7.5
        dialog.forms['connection']._controls['source'].setText('unique-source')
        dialog.accept()
        return dialog.result()

    monkeypatch.setattr(module.DeviceDefinitionDialog, 'exec_', edit)
    monkeypatch.setattr(QMessageBox, 'question', lambda *_: QMessageBox.No)
    try:
        waitUntil(lambda: bool(wizard.types))
        before = window.pageCoordinator.session.payload()
        wizard._create('camera')
        assert len(opened) == 1
        key = wizard.slots['camera'][0].currentData()
        assert wizard.newDefinitions[key]['connection']['source'] == 'unique-source'
        assert wizard.newDefinitions[key]['defaults']['gain'] == 7.5
        assert window.pageCoordinator.session.payload() == before
        wizard.reject()
        assert window.pageCoordinator.session.payload() == before
    finally:
        wizard.reject()
