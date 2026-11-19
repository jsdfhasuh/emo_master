"""Edit the public Qt forms, save the current project, and reopen the same fields."""
import hashlib

from PySide2.QtCore import QTimer
from PySide2.QtGui import QImage
from PySide2.QtWidgets import QApplication, QCheckBox, QFormLayout, QGroupBox

from emo_master.apps.designer.page_designer.appearance_properties import (
    editComponentAppearance, editPageAppearance,
)
from emo_master.apps.designer.state.project_store import loadProjectDocument, saveProject
from emo_master.apps.designer.state.workflow_store import WorkflowStore
from emo_master.core.presentation.models import Component, Page, Props
from emo_master.core.project.models import ProjectDocument
from emo_master.core.project.resources import Resource


def _group(dialog, title):
    return next(group for group in dialog.findChildren(QGroupBox) if group.title() == title)


def _field(group, title):
    form = group.layout()
    for row in range(form.rowCount()):
        label = form.itemAt(row, QFormLayout.LabelRole)
        if label is not None and label.widget().text() == title:
            return form.itemAt(row, QFormLayout.FieldRole).widget()
    raise AssertionError(f'Missing field: {title}')


def _toggle(dialog, title):
    return next(box for box in dialog.findChildren(QCheckBox) if box.text() == title)


def _choose(field, value):
    index = field.findData(value)
    assert index >= 0, value
    field.setCurrentIndex(index)


def _runDialog(openDialog, edit):
    errors = []

    def interact():
        dialog = QApplication.activeModalWidget()
        try:
            edit(dialog)
            dialog.accept()
            assert not dialog.error.text(), dialog.error.text()
        except Exception as error:
            errors.append(error)
            dialog.reject()

    QTimer.singleShot(0, interact)
    result = openDialog()
    if errors:
        raise errors[0]
    assert result is not None
    return result


def testQtHeaderControlsSaveAndReopen(designerApplication, tmp_path):
    document = ProjectDocument.model_validate(WorkflowStore().toPayload('Configurable page'))
    logo = QImage(80, 40, QImage.Format_RGB32)
    logo.fill(0xff2459d1)
    (tmp_path / 'assets').mkdir()
    logoPath = tmp_path / 'assets' / 'logo.png'
    assert logo.save(str(logoPath))
    data = logoPath.read_bytes()
    document.resources.items['logo'] = Resource(path='assets/logo.png', purpose='icon',
        sha256=hashlib.sha256(data).hexdigest(), size=len(data))

    def configure(dialog):
        header = _group(dialog, '页头（勾选以自定义）')
        groups = [_group(dialog, title) for title in (
            '页头表面（勾选以自定义）', '页头文字（勾选以自定义）',
            '开始按钮', '开始按钮外观（勾选以自定义）', '停止按钮', '停止按钮外观（勾选以自定义）',
        )]
        assert not header.isChecked() and all(not group.isEnabled() for group in groups)
        startLabel = _field(_group(dialog, '开始按钮'), '按钮文字（留空使用宿主默认）')
        assert not startLabel.isEnabled()
        header.setChecked(True)
        assert startLabel.isEnabled() and all(group.isEnabled() for group in groups)
        _field(header, '标题').setText('装配视觉')
        _field(header, '副标题').setText('生产工位')
        _choose(_field(header, '标志图片'), 'logo')
        _field(header, 'Logo 显示宽度').setValue(80)
        _field(header, 'Logo 显示高度').setValue(40)
        surface = _group(dialog, '页头表面（勾选以自定义）')
        surface.setChecked(True)
        _choose(_field(surface, '背景'), 'surface')
        _field(surface, '圆角').setValue(16)
        typography = _group(dialog, '页头文字（勾选以自定义）')
        typography.setChecked(True)
        _field(typography, '标题字号').setValue(32)
        for action, label, style in [('开始', '开始检测', 'primary'), ('停止', '停止检测', 'secondary')]:
            _field(_group(dialog, action + '按钮'), '按钮文字（留空使用宿主默认）').setText(label)
            appearance = _group(dialog, action + '按钮外观（勾选以自定义）')
            appearance.setChecked(True)
            _choose(_field(appearance, '按钮样式'), style)
            _field(appearance, '按钮文字字号').setValue(20)
            _field(appearance, '圆角').setValue(10)
            _field(appearance, '最小宽度').setValue(128)
            _field(appearance, '最小高度').setValue(48)
        header.setChecked(False)
        assert all(not group.isEnabled() for group in groups)
        assert surface.isChecked() and typography.isChecked()
        header.setChecked(True)
        assert surface.isChecked() and typography.isChecked()
        assert startLabel.text() == '开始检测'

    appearance, shell, mode = _runDialog(
        lambda: editPageAppearance(None, document.presentation, document.resources.items), configure)
    document.presentation.appearance, document.presentation.shell = appearance, shell
    document.presentation.navigation.mode = mode
    path = tmp_path / 'configured.emoproj'
    saveProject(path, document.model_dump(mode='json'))
    restored = loadProjectDocument(path)
    assert restored.presentation.shell == shell
    assert shell.startControl.label == '开始检测' and shell.stopControl.label == '停止检测'
    assert shell.startControl.appearance.minWidth == 128
    assert shell.logoResourceId == 'logo' and (shell.logoWidth, shell.logoHeight) == (80, 40)

    def inspect(dialog):
        header = _group(dialog, '页头（勾选以自定义）')
        assert _field(header, 'Logo 显示宽度').value() == 80
        assert _field(header, '标志图片').currentData() == 'logo'
        assert _field(_group(dialog, '开始按钮'), '按钮文字（留空使用宿主默认）').text() == '开始检测'
        assert _field(_group(dialog, '停止按钮'), '按钮文字（留空使用宿主默认）').text() == '停止检测'
        assert _field(_group(dialog, '页头表面（勾选以自定义）'), '圆角').value() == 16
        assert _field(_group(dialog, '页头文字（勾选以自定义）'), '标题字号').value() == 32

    assert _runDialog(lambda: editPageAppearance(None, restored.presentation, restored.resources.items), inspect)[1] == shell


def testQtIndicatorAndHistoryFieldsPersistThroughProjectSave(designerApplication, tmp_path):
    document = ProjectDocument.model_validate(WorkflowStore().toPayload('History configuration'))
    indicator = Component(componentId='state', type='indicator', props={'title': 'Inspection'})
    records = Component(componentId='records', type='sqlite_table', props={
        'sqlite': {'databasePath': 'history.db', 'table': 'records', 'columns': ['id', 'result']},
    })
    page = Page(name='Review', components=[indicator, records])
    document.presentation.pages = {'review': page}
    document.presentation.pageOrder = ['review']
    document.presentation.defaultPageId = 'review'

    def indicatorFields(dialog):
        display = _group(dialog, '显示')
        _choose(_field(display, '状态样式'), 'row')
        _choose(_field(display, '圆点位置'), 'title')
        _toggle(dialog, '标题跟随状态颜色').setChecked(True)

    def historyFields(dialog):
        history = _group(dialog, '历史列表')
        _choose(_field(history, '显示方式'), 'list')
        _choose(_field(history, '标题字段'), 'id')
        for label in ('显示筛选栏', '显示排序控制', '显示分页栏'):
            _toggle(dialog, label).setChecked(False)

    for component, edit in [(indicator, indicatorFields), (records, historyFields)]:
        changed = _runDialog(lambda: editComponentAppearance(None, component, page), edit)
        component.props = Props.model_validate({**component.props.model_dump(), **changed})
    path = tmp_path / 'configured.emoproj'
    saveProject(path, document.model_dump(mode='json'))
    page = loadProjectDocument(path).presentation.pages['review']
    indicator, records = page.components
    assert indicator.props.titleFollowsState and indicator.props.markerPosition == 'title'
    assert records.props.historyView.mode == 'list'

    def inspectIndicator(dialog):
        assert _toggle(dialog, '标题跟随状态颜色').isChecked()
        assert _field(_group(dialog, '显示'), '圆点位置').currentData() == 'title'

    def inspectHistory(dialog):
        history = _group(dialog, '历史列表')
        assert _field(history, '标题字段').currentData() == 'id'
        _choose(_field(history, '显示方式'), 'table')
        for label in ('显示筛选栏', '显示排序控制', '显示分页栏'):
            assert not _toggle(dialog, label).isChecked()

    _runDialog(lambda: editComponentAppearance(None, indicator, page), inspectIndicator)
    changed = _runDialog(lambda: editComponentAppearance(None, records, page), inspectHistory)
    assert changed['historyView']['mode'] == 'table'
    assert changed['historyView']['titleField'] == 'id'
    assert all(changed['historyView'][key] is False for key in ('showFilter', 'showSort', 'showPagination'))
