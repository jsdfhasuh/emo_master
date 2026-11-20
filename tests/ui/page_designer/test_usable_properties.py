"""Typed property controls operate on the existing draft and shared renderer."""
from dataclasses import asdict
import json

import pytest
from PySide2.QtCore import QTimer, Qt
from PySide2.QtGui import QPalette
from PySide2.QtTest import QTest
from PySide2.QtWidgets import QApplication, QCheckBox, QDialog, QFormLayout, QGroupBox, QLabel, QPlainTextEdit, QPushButton, QWidget

from test_workspace import designer  # noqa: F401
from test_pending_inputs import setupPage
from test_editing import blank, metadata
from test_preview import waitFor
from emo_master.apps.designer.page_designer.appearance_properties import SettingsDialog
from emo_master.apps.designer.page_designer.appearance_preview import ComponentAppearancePreview
from emo_master.apps.designer.state.presentation_store import _component
from emo_master.core.presentation.models import ResultScope


def testStaticResourcePickerPersistsAndUndoRestores(designer, tmp_path):  # noqa: F811
    import hashlib
    from PySide2.QtGui import QImage, QColor
    coordinator, editor, key = setupPage(designer, 'image')
    coordinator.directory = tmp_path
    picture = QImage(80, 48, QImage.Format_ARGB32)
    picture.fill(QColor('#c94054'))
    assert picture.save(str(tmp_path / 'page.png'))
    data = (tmp_path / 'page.png').read_bytes()
    with coordinator.session.transaction():
        coordinator.session.workflows.projectExtensions['resources']['items']['page-image'] = dict(
            path='page.png', size=len(data), sha256=hashlib.sha256(data).hexdigest(), purpose='page_image')
    editor.refresh()
    editor.tools.select(key)
    editor.tools.imageProperties.mode.setCurrentIndex(editor.tools.imageProperties.mode.findData('resource'))
    picker = editor.tools.fields['imageResourceId']
    assert picker.findData('page-image') >= 0
    picker.setCurrentIndex(picker.findData('page-image'))
    editor.tools.commitPending()
    assert _component(editor.store.snapshot(), editor.pageId, key).props.imageResourceId == 'page-image'
    editor.refresh()
    assert not editor.renderer.widgets[editor.pageId][key][1].image.isNull()
    assert not editor.renderer.imageSources()
    assert coordinator.session.undo()
    assert _component(editor.store.snapshot(), editor.pageId, key).props.imageResourceId is None
    assert coordinator.session.redo()
    assert designer.saveProjectToDirectory(str(tmp_path / 'saved'))
    saved = json.loads((tmp_path / 'saved' / 'project.json').read_text(encoding='utf-8'))
    assert saved['presentation']['pages'][editor.pageId]['components'][0]['props']['imageResourceId'] == 'page-image'
    assert (tmp_path / 'saved' / 'page.png').read_bytes() == data


def testTypedIndicatorPlainTextFalseAndAppearanceSave(designer, tmp_path, monkeypatch):  # noqa: F811
    coordinator, editor, key = setupPage(designer, 'indicator')
    tools = editor.tools
    tools.addExtraRow(values=('string', 'OK', '合格', 'green', 'none'))
    tools.addExtraRow(values=('boolean', False, '不合格', 'red', 'soft'))
    tools.fields['fontSize'].setValue(32)

    def chooseAppearance(dialog):
        groups = {group.title(): group for group in dialog.findChildren(QGroupBox)}
        for title, choices in [('表面（勾选以自定义）', {'类型': 'card', '背景': 'subtle'}),
                               ('文字（勾选以自定义）', {'主值字重': 'bold'})]:
            group = groups[title]
            group.setChecked(True)
            form = group.layout()
            for row in range(form.rowCount()):
                name = form.itemAt(row, QFormLayout.LabelRole).widget().text()
                if name in choices:
                    field = form.itemAt(row, QFormLayout.FieldRole).widget()
                    field.setCurrentIndex(field.findData(choices[name]))
        dialog.accept()
        assert dialog.result() == QDialog.Accepted
        return dialog.result()

    monkeypatch.setattr(SettingsDialog, 'exec_', chooseAppearance)
    tools.compositionButton.click()
    assert designer.saveProjectToDirectory(str(tmp_path / 'typed'))
    saved = json.loads((tmp_path / 'typed' / 'project.json').read_text(encoding='utf-8'))
    props = saved['presentation']['pages'][editor.pageId]['components'][0]['props']
    assert set(props['indicatorStates']) == {'"OK"', 'false'}
    assert props['indicatorStates']['false']['background'] == 'soft'
    assert props['typography']['value']['size'] == 32 and props['typography']['value']['weight'] == 'bold'
    assert props['surface']['kind'] == 'card' and props['surface']['background'] == 'subtle'
    editor.refresh()
    tools.select(key)
    assert tools.extra.item(0, 1).text() == 'OK'
    assert tools.extra.item(1, 1).text() == 'false'
    assert tools.extra.cellWidget(1, 0).currentData() == 'boolean'
    assert tools.extra.cellWidget(1, 4).currentData() == 'soft'
    assert not coordinator.session.dirty


def testAdvancedAppearanceDraftPreviewAndUndo(designer, tmp_path):  # noqa: F811
    coordinator, editor, key = setupPage(designer, 'number')
    tools = editor.tools
    pageId = editor.pageId
    originalCode = '*[emoPart="value"] { font-weight: bold; }'
    unsupportedCode = '*[emoPart="value"] { qproperty-wordWrap: false; }'
    webCode = '[data-emo-part="value"] { color: #214D85; }'

    def component():
        return _component(coordinator.session.presentation.snapshot(), pageId, key)

    def seed(presentation):
        item = _component(presentation, pageId, key)
        from emo_master.core.presentation.models import Props
        item.props = Props.model_validate({
            **item.props.model_dump(),
            'surface': {'background': '#F0F5FC', 'borderStyle': 'dashed'},
            'customStyle': {
                'qtQss': {'enabled': True, 'code': originalCode},
                'webCss': {'enabled': True, 'code': webCode},
            },
        })
    coordinator.session.editPresentation(seed)
    editor.refresh()
    tools.select(key)
    tools.fields['title'].setText('打开前的待提交标题')

    def edit(interaction):
        errors = []

        def interact():
            dialog = QApplication.activeModalWidget()
            try:
                assert isinstance(dialog, SettingsDialog)
                code = dialog.findChild(QPlainTextEdit, 'advancedQssCode')
                assert code is not None
                interaction(dialog, code)
            except BaseException as error:
                errors.append(error)
                if dialog:
                    dialog.reject()

        QTimer.singleShot(20, interact)
        tools.advancedAppearanceButton.click()
        if errors:
            raise errors[0]

    def assertPreviewWeight(dialog, bold):
        def hasWeight():
            for stage in dialog.findChildren(ComponentAppearancePreview):
                if not stage.isHidden():
                    values = stage.componentCard.findChildren(QWidget)
                    return any(widget.font().bold() == bold for widget in values
                               if widget.property('emoPart') == 'value')
            return False
        waitFor(hasWeight, seconds=3)

    def invalidThenEscape(dialog, code):
        assertPreviewWeight(dialog, True)
        code.setPlainText('*[emoPart="value"] { font-weight:')
        assertPreviewWeight(dialog, False)
        assert '未应用' in dialog.findChild(QLabel, 'advancedQssFeedback').text()
        dialog.accept()
        assert dialog.isVisible() and dialog.error.text()
        assert code.toPlainText().endswith('font-weight:')
        code.setPlainText(unsupportedCode)
        feedback = dialog.findChild(QLabel, 'advancedQssFeedback')
        waitFor(lambda: '未应用' in feedback.text() and 'qproperty-*' in feedback.text(), seconds=3)
        assertPreviewWeight(dialog, False)
        dialog.accept()
        assert dialog.isVisible() and 'qproperty-*' in dialog.error.text()
        assert code.toPlainText() == unsupportedCode
        code.setPlainText(originalCode + '\n/* 已修正的草稿 */')
        assertPreviewWeight(dialog, True)
        QTest.keyClick(dialog, Qt.Key_Escape)

    edit(invalidThenEscape)
    assert component().props.title == '打开前的待提交标题'
    assert component().props.customStyle.qtQss.code == originalCode

    def clear(dialog):
        dialog.findChild(QPushButton, 'advancedQssClear').click()
        assertPreviewWeight(dialog, False)
        assert dialog.findChild(QCheckBox, 'advancedQssEnabled').isChecked()

    def clearThenClose(dialog, _):
        clear(dialog)
        dialog.close()

    edit(clearThenClose)
    assert component().props.customStyle.qtQss.code == originalCode

    def clearThenAccept(dialog, _):
        clear(dialog)
        dialog.accept()

    edit(clearThenAccept)
    props = component().props
    assert props.customStyle.qtQss.enabled and not props.customStyle.qtQss.code
    assert props.customStyle.webCss.code == webCode and props.customStyle.webCss.enabled
    assert props.surface.background == '#F0F5FC' and props.surface.borderStyle == 'dashed'
    assert coordinator.session.undo()
    assert component().props.title == '打开前的待提交标题'
    assert component().props.customStyle.qtQss.code == originalCode
    assert coordinator.session.redo()
    assert component().props.customStyle.qtQss.code == ''
    assert coordinator.session.undo()
    original = component().props
    tools.select(key)
    tools.operationButtons[0].click()
    copied = tools.selected
    assert copied != key
    copiedComponent = _component(coordinator.session.presentation.snapshot(), pageId, copied)
    assert copiedComponent.props.customStyle == original.customStyle

    def values():
        return tuple(editor.renderer.widgets[pageId][componentId][1].value for componentId in (key, copied))

    assert all(value.font().bold() for value in values())
    copiedCode = '*[emoPart="value"] { color: #995305; font-weight: normal; }'

    def editCopiedCode(dialog, code):
        code.setPlainText(copiedCode)
        assertPreviewWeight(dialog, False)
        dialog.accept()

    edit(editCopiedCode)
    originalValue, copiedValue = values()
    assert originalValue.font().bold() and not copiedValue.font().bold()
    assert originalValue.palette().color(QPalette.WindowText).name() != '#995305'
    assert copiedValue.palette().color(QPalette.WindowText).name() == '#995305'

    def saveDisabledText(dialog, code):
        code.setPlainText(unsupportedCode)
        dialog.findChild(QCheckBox, 'advancedQssEnabled').setChecked(False)
        dialog.accept()

    edit(saveDisabledText)
    copiedStyle = _component(coordinator.session.presentation.snapshot(), pageId, copied).props.customStyle
    assert not copiedStyle.qtQss.enabled and copiedStyle.qtQss.code == unsupportedCode
    assert copiedStyle.webCss == original.customStyle.webCss
    assert component().props == original
    cards = editor.renderer.cards[pageId]
    assert cards[key].property('emoAppearanceOwner') != cards[copied].property('emoAppearanceOwner')

    path = tmp_path / 'advanced'
    assert designer.saveProjectToDirectory(str(path))
    assert designer.loadProjectDirectory(str(path), confirmed=True)
    assert component().props == original


def testDuplicateTypedValuesPreservePendingFormAndDraft(designer):  # noqa: F811
    coordinator, editor, key = setupPage(designer, 'indicator')
    for title in ('first', 'second'):
        editor.tools.addExtraRow(values=('string', 'OK', title, 'green', 'none'))
    before = coordinator.session.payload()
    with pytest.raises(ValueError, match='duplicate'):
        editor.tools.commitPending()
    assert coordinator.session.payload() == before
    assert editor.tools.extra.rowCount() == 2
    editor.tools.extra.setRowCount(0)


def testKnownCollectionFieldsAreSelectedWithoutJsonPaths(designer, tmp_path):  # noqa: F811
    coordinator = designer.pageCoordinator
    designer.workflowController.loadPayload(blank(tmp_path).model_dump())
    coordinator.session.acceptLoaded()
    waitFor(lambda: designer.operatorCatalogController.state == 'ready')
    designer.operatorCatalog = [asdict(m) for m in metadata().values()]
    coordinator.showPages()
    editor = coordinator.editor
    editor.pageId = editor.store.createPage('集合')
    key = editor.tools.commands().add(editor.pageId, 'table', 0, 0)
    editor.refresh()
    index = next(i for i, choice in enumerate(editor.tools.choices)
                 if choice.source.nodeId == 'blob' and choice.source.port == 'blobs' and not choice.source.fieldPath)
    editor.tools.bindChoice(index, key)
    editor.refresh()
    editor.tools.select(key)
    editor.tools.addExtraRow()
    picker = editor.tools.extra.cellWidget(0, 1)
    assert {picker.itemData(i) for i in range(picker.count())} == {('area',), ('centroid', 'x'), ('centroid', 'y')}
    picker.setCurrentIndex(picker.findText('centroid.x'))
    editor.tools.commitPending()
    assert _component(editor.store.snapshot(), editor.pageId, key).props.columns[0].fieldPath == ['centroid', 'x']


def testAllReadOnlyActionsEditedWithExplicitScope(designer):  # noqa: F811
    coordinator, editor, key = setupPage(designer, 'navigation_button')
    first = editor.pageId
    second = editor.store.createPage('详情')
    workflow = coordinator.session.document().entryWorkflowId
    def configure(p):
        p.resultScopes['shown'] = ResultScope(entryWorkflowId=workflow, scopeWorkflowId=workflow)
        p.pages[first].resultScopeIds = ['shown']
        p.pages[second].resultScopeIds = ['shown']
    coordinator.session.editPresentation(configure)
    editor.refresh()
    editor.tools.select(key)
    tools = editor.tools
    for mode in ('navigate', 'detail', 'freeze', 'resume_live', 'none'):
        tools.actionMode.setCurrentIndex(tools.actionMode.findData(mode))
        tools.destination.setCurrentIndex(tools.destination.findData(second))
        tools.actionScope.setCurrentIndex(tools.actionScope.findData('shown'))
        tools.commitPending()
        action = _component(editor.store.snapshot(), first, key).actions.get('clicked')
        if mode == 'none':
            assert action is None
        elif mode == 'detail':
            assert action.type == 'navigate' and action.context == 'displayed_result' and action.resultScopeId == 'shown'
        else:
            assert action.type == mode
    assert designer.currentJobId is None


def testOfflineStatesDoNotTouchRuntimeOrDirtyDraft(designer):  # noqa: F811
    coordinator, editor, key = setupPage(designer, 'indicator')
    editor.tools.addExtraRow(values=('boolean', True, '模拟合格', 'green', 'none'))
    editor.tools.addExtraRow(values=('boolean', False, '模拟不合格', 'red', 'none'))
    editor.tools.commitPending()
    editor.refresh()
    coordinator.session.markSaved()
    history = len(coordinator.session._undo)
    preview = coordinator.preview.openObserver()
    for state, text, diagnostic in [('OK', '模拟合格', ''), ('NG', '模拟不合格', ''),
                                    ('WAITING', '—', '模拟 · 等待触发 / 尚无结果'),
                                    ('ERROR', '—', '模拟 · NODE_FAILED · 示例错误')]:
        preview.simulation.setCurrentIndex(preview.simulation.findData(state))
        _, indicator = preview.widgets[editor.pageId][key]
        assert indicator.text() == text
        assert indicator.toolTip() == diagnostic
        assert '模拟状态' in preview.banner.text()
        assert not preview.displayed
    preview.simulation.setCurrentIndex(0)
    _, indicator = preview.widgets[editor.pageId][key]
    assert not indicator.toolTip()
    assert not coordinator.session.dirty and len(coordinator.session._undo) == history
    assert coordinator.preview.session is None and coordinator.preview.backend is None
    assert designer.currentJobId is None
