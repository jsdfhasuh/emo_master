"""Device creation stays editable until validation and explicit reuse succeed."""
from concurrent.futures import Future
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide2.QtWidgets import QApplication, QMessageBox, QTabWidget, QWidget

from emo_master.apps.designer.ui import device_wizard as module
from emo_master.core.plugin.devices import DeviceType
from tests.designer.test_device_identity_reuse import builtin
from tests.designer.test_device_workspace import windowFor, waitUntil
from tests.core.project.test_devices import declaration, definition  # noqa: F401
from tests.runtime.test_tcp_device_integration import definition as tcpDefinition


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


@pytest.mark.parametrize('invalid', ['address', 'address-multicast', 'channel', 'duplicate-route'])
def testTcpOuterValidationFocusesExactConnectionFieldAndRetainsDraft(retainedQtApplication, invalid):
    path = Path(__file__).resolve().parents[2]/'src/emo_master/plugins/builtins/tcp_session/device_types/server.json'
    metadata = DeviceType.model_validate_json(path.read_text(encoding='utf-8'))
    initial = tcpDefinition(metadata, channels=[dict(channelId='a', name='A', allowedIps=['127.0.0.1'])])
    commits, runtimeCalls = [], []
    client = SimpleNamespace(deviceNetworkCommand=lambda *args, **kwargs: runtimeCalls.append((args, kwargs)))
    dialog = module.DeviceDefinitionDialog(metadata, initial=initial, client=client,
        beforeAccept=lambda value: commits.append(deepcopy(value)))
    dialog.show()
    dialog.activateWindow()
    try:
        waitUntil(dialog.isActiveWindow)
        form = dialog.forms['connection']
        if invalid == 'address':
            form.fields._controls['bindAddress'].setText('127.0.0.999')
        elif invalid == 'address-multicast':
            form.fields._controls['bindAddress'].setText('224.0.0.1')
        elif invalid == 'channel':
            form.channels.setValue([dict(channelId='a', name='A', allowedIps=['invalid'])])
        else:
            form.channels.setValue([dict(channelId=key, name=key, allowedIps=['127.0.0.1']) for key in ('a', 'b')])
        candidate = deepcopy(form.getValues())
        dialog.tabs.setCurrentIndex(1)
        dialog.accept()
        QApplication.processEvents()
        assert dialog.isVisible() and dialog.value is None and dialog.result() != dialog.Accepted
        assert dialog.error.text() and dialog.tabs.currentIndex() == 0
        assert form.getValues() == candidate
        assert not commits and not runtimeCalls
        target = form.fields._controls['bindAddress'] if invalid.startswith('address') else form.channels.table
        assert target.hasFocus(), type(QApplication.focusWidget()).__name__
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


def testDisabledEmptyServerCanBeCreatedAndEnabledErrorFindsChannels(retainedQtApplication):
    path = Path(__file__).resolve().parents[2]/'src/emo_master/plugins/builtins/tcp_session/device_types/server.json'
    metadata = DeviceType.model_validate_json(path.read_text(encoding='utf-8'))
    dialog = module.DeviceDefinitionDialog(metadata, initial=dict(enabled=False, description='分阶段配置'))
    dialog.show()
    try:
        assert not dialog.enabled.isChecked()
        value = dialog.definition()
        assert not value['enabled'] and value['connection']['channels'] == []
        assert value['description'] == '分阶段配置'
        dialog.enabled.setChecked(True)
        dialog.accept()
        QApplication.processEvents()
        assert dialog.value is None and dialog.isVisible()
        assert '请添加至少一个客户端通道' in dialog.error.text()
        assert dialog.forms['connection'].channels.table.hasFocus()
    finally:
        dialog.reject()


def testEmptyTcpIpFindsExactCellAndGivesChineseFeedback(retainedQtApplication):
    path = Path(__file__).resolve().parents[2]/'src/emo_master/plugins/builtins/tcp_session/device_types/server.json'
    metadata = DeviceType.model_validate_json(path.read_text(encoding='utf-8'))
    dialog = module.DeviceDefinitionDialog(metadata)
    dialog.show()
    try:
        table = dialog.forms['connection'].channels.table
        dialog.forms['connection'].channels.setValue([dict(channelId='a', name='A', allowedIps=[])])
        dialog.tabs.setCurrentIndex(1)
        dialog.accept()
        QApplication.processEvents()
        assert dialog.value is None and dialog.isVisible()
        assert '通道 1：请填写至少一个允许接入的客户端 IPv4' in dialog.error.text()
        assert table.hasFocus() and (table.currentRow(), table.currentColumn()) == (0, 2)
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


@pytest.mark.parametrize('failed', [False, True])
def testCreationConnectionReceiptBelongsToCandidate(retainedQtApplication, monkeypatch, failed):
    path = Path(__file__).resolve().parents[2]/'src/emo_master/plugins/builtins/tcp_session/device_types/server.json'
    metadata = DeviceType.model_validate_json(path.read_text(encoding='utf-8'))
    initial = tcpDefinition(metadata, port=10000, channels=[])
    initial['enabled'] = False
    dialog = module.DeviceDefinitionDialog(metadata, initial=initial, client=SimpleNamespace())
    future = Future()
    monkeypatch.setattr(dialog.setupPool, 'submit', lambda *_args, **_kw: future)
    monkeypatch.setattr(QMessageBox, 'question', lambda *_: QMessageBox.Yes)
    dialog.show()
    try:
        dialog.testConnection()
        assert dialog.setupPending is future
        dialog.forms['connection'].fields._controls['port'].setValue(10001)
        dialog.error.setText('当前输入待测试')
        if failed:
            future.set_exception(ValueError('OLD-ENDPOINT-FAILURE'))
        else:
            future.set_result(dict(message='OLD-ENDPOINT-SUCCESS'))
        dialog.setupReady()
        assert dialog.setupPending is None and not dialog.setupTimer.isActive()
        assert dialog.error.text() == '当前输入待测试'
        assert 'OLD-ENDPOINT' not in dialog.error.toolTip()
        assert dialog.value is None
    finally:
        dialog.setupPending = None
        dialog.reject()


def testMissingSlotFeedbackClearsOnlyWhenThatSlotIsSelected(ownedDesignerWindow, tmp_path,
        declaration, definition):  # noqa: F811
    window, requirements = windowFor(ownedDesignerWindow, tmp_path, definition, declaration)
    requirements['vision.io.huaray_camera']['other'] = deepcopy(requirements['vision.io.huaray_camera']['camera'])
    requirements = requirements['vision.io.huaray_camera']
    wizard = module.DeviceNodeDialog(window.pageCoordinator,
        dict(nodeId='new', operatorId='vision.io.huaray_camera', params={}), requirements, window)
    wizard.show()
    try:
        waitUntil(lambda: bool(wizard.types))
        wizard.accept()
        message = '请选择设备：camera'
        assert wizard.error.text() == message
        other = wizard.slots['other'][0]
        other.setCurrentIndex(other.findData('camera'))
        assert wizard.error.text() == message
        camera = wizard.slots['camera'][0]
        camera.setCurrentIndex(camera.findData('camera'))
        assert 'camera' in wizard.sources
        assert not wizard.error.text()
        assert wizard.result() != wizard.Accepted
    finally:
        wizard.reject()
