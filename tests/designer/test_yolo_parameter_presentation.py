"""Human-readable controls through the actual operator workspace window."""
from copy import deepcopy
import os
from pathlib import Path

import pytest
from PySide2.QtCore import QPoint, Qt
from PySide2.QtTest import QTest
from PySide2.QtWidgets import QLineEdit

from emo_master.apps.designer.operator_editors.controller_protocol import EditorContext, EditorKey
from emo_master.apps.designer.operator_editors.workspace_window import OperatorWorkspaceWindow
from emo_master.apps.designer.state.schema_utils import mergeParameterTitles
from emo_master.plugins.builtins.yolo_inference.operator import YoloInferenceOperator
from tests.runtime.test_global_variables import variable


def workspace(*, variables=None, oldSchema=False):
    schema = deepcopy(YoloInferenceOperator.meta.paramSchema)
    if oldSchema:
        schema['properties']['classes'].pop('xWidget')
        schema['properties']['classes'].pop('xPlaceholder')
        schema['properties']['drawOverlay']['default'] = False
    savedSchema = deepcopy(schema)
    schema = mergeParameterTitles(schema, YoloInferenceOperator.meta.paramSchema)
    applied = []
    context = EditorContext(key=EditorKey('p', 'main', 'yolo'),
        operatorId='vision.inference.yolo', version=YoloInferenceOperator.meta.version, previewMode='none',
        paramSchema=schema, runtimeClient=None, applyParams=lambda *_args: True,
        appendLog=lambda *_args: None, variableDefinitions=lambda: variables or {}, variableBindings=[],
        applyConfiguration=lambda *args: applied.append(args) or True)
    values = dict(modelPath='D:/models/yolo11n.onnx', confidence=.25, iou=.45, imageSize=1280,
                  maxDetections=300, device='cpu', classes=[0, 1, 3], agnosticNms=False,
                  drawOverlay=not oldSchema, outputCrop=False, cropTargetIndex=1)
    window = OperatorWorkspaceWindow(key=context.key, title='YOLO 检测全部目标', context=context,
                                     schema=schema, values=values)
    window.bindDebug(lambda: None)
    return window, applied, values, savedSchema


def testOldYoloSchemaGetsReadableControlsWithoutChangingParameters():
    window, applied, original, savedSchema = workspace(oldSchema=True)
    try:
        form = window._schemaForm
        assert isinstance(form._controls['classes'], QLineEdit)
        assert form._controls['classes'].text() == '0, 1, 3'
        assert form._controls['drawOverlay'].text() == '已关闭'
        assert not window.isDirty()
        assert window.collectParams() == original
        assert window.context.paramSchema['properties']['drawOverlay']['default'] is False
        assert 'xWidget' not in savedSchema['properties']['classes']
        assert window.applyChanges()
        assert applied[-1][1] == original and applied[-1][2] == []
        form._controls['classes'].clear()
        assert form._controls['classes'].placeholderText() == '全部类别'
        assert window.applyChanges()
        assert applied[-1][1]['classes'] == []
        assert original['classes'] == [0, 1, 3]
    finally:
        window.forceClose()


@pytest.mark.parametrize('width', [460, 620])
def testCompactYoloWindowKeepsValuesSourcesAndActionsUsable(designerApplication, width):
    variables = {'overlay': variable(True, 'boolean', name='绘制检测框'),
                 'limit': variable(300, 'integer', name='最大目标数量'),
                 'confidence': variable(.25, 'number', name='检测置信度阈值'),
                 'device': variable('cpu', 'string', name='推理设备')}
    window, applied, original, _ = workspace(variables=variables)
    try:
        form = window._schemaForm
        window.resize(width, 720)
        window.show()
        designerApplication.processEvents()
        assert window.width() == width
        assert form._controls['imageSize'].suffix() == ' 像素'
        assert form._controls['maxDetections'].suffix() == ' 个'
        assert form._controls['drawOverlay'].text() == '已开启'
        assert form._controls['outputCrop'].text() == '已关闭'
        assert not form._controls['cropTargetIndex'].isEnabled()
        assert form._fieldContainers['cropTargetIndex'].isHidden()
        assert window.collectParams() == original and not window.isDirty()
        assert form._controls['classes'].height() < 50
        form._controls['outputCrop'].setChecked(True)
        designerApplication.processEvents()
        assert not form._fieldContainers['cropTargetIndex'].isHidden()
        assert form._controls['cropTargetIndex'].isEnabled()
        for name, source in form._variableSources.items():
            control = form._controls[name]
            box = form._fieldContainers[name]
            assert control.geometry().right() < source.geometry().left()
            assert abs(control.geometry().center().y() - source.geometry().center().y()) <= 1
            assert box.rect().contains(control.geometry()) and box.rect().contains(source.geometry())
            label = form._layout.labelForField(box)
            assert not label.geometry().intersects(box.geometry())
        assert not window._bindingScroll.isVisible()
        assert not window._applyButton.visibleRegion().isEmpty()
        assert not window._closeButton.visibleRegion().isEmpty()
        form._controls['outputCrop'].setChecked(False)
        designerApplication.processEvents()
        screenshotDir = os.environ.get('EMO_YOLO_FORM_SCREEN_DIR')
        if screenshotDir:
            target = Path(screenshotDir)
            target.mkdir(parents=True, exist_ok=True)
            assert window.grab().save(str(target / f'yolo-parameters-{width}.png'))

        control = form._controls['drawOverlay']
        form.focusField('drawOverlay')
        designerApplication.processEvents()
        QTest.mouseClick(control, Qt.LeftButton, pos=QPoint(8, control.height() // 2))
        assert control.text() == '已关闭' and not control.isChecked()
        source = form._variableSources['drawOverlay']
        source.setCurrentIndex(source.findData('overlay'))
        assert not control.isEnabled() and not control.isChecked()
        assert window.applyChanges()
        assert applied[-1][1]['drawOverlay'] is False
        assert applied[-1][2] == [{'parameterPath': ['drawOverlay'], 'variableId': 'overlay'}]
        source.setCurrentIndex(source.findData(''))
        assert control.isEnabled() and control.text() == '已关闭'
        form._controls['classes'].setText('0，3')
        assert window.applyChanges()
        assert applied[-1][1]['classes'] == [0, 3] and applied[-1][2] == []
    finally:
        window.forceClose()


def testNoCompatibleVariablesDoNotShowRedundantFixedValueSelectors(designerApplication):
    window, _applied, original, _ = workspace(variables={'enabled': variable(True, 'boolean')})
    try:
        window.show()
        designerApplication.processEvents()
        form = window._schemaForm
        assert form._variableSources['confidence'].isHidden()
        assert form._variableSources['imageSize'].isHidden()
        assert not form._variableSources['drawOverlay'].isHidden()
        assert window.collectParams() == original
        assert not window.isDirty()
    finally:
        window.forceClose()
