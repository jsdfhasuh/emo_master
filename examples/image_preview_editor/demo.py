"""Run with python -m examples.image_preview_editor.demo from the repository."""
from pathlib import Path

import emo_master  # noqa: F401 - initialize Windows DLL search before Qt.
from PySide2.QtCore import Qt
from PySide2.QtGui import QPainter, QPixmap
from PySide2.QtWidgets import QApplication

from emo_master.apps.designer.main import applyDesignerStyle
from emo_master.apps.designer.operator_editors.controller_protocol import EditorContext, EditorKey
from emo_master.apps.designer.operator_editors.ui_loader import loadUiBytes
from emo_master.apps.designer.operator_editors.workspace_window import OperatorWorkspaceWindow
from .editor import ExampleEditorController


def createWindow():
    key = EditorKey("example", "main", "preview")
    context = EditorContext(key=key, operatorId="example.image_preview", version="1.0.0",
        previewMode="pure", paramSchema={"type": "object", "properties": {}},
        runtimeClient=object(), applyParams=lambda *_: True, appendLog=lambda *_: None,
        variableDefinitions=lambda: {}, variableBindings=[])
    controller = ExampleEditorController()
    window = OperatorWorkspaceWindow(key=key, title="图片预览控件示例", context=context,
        schema=context.paramSchema, values={}, controller=controller,
        customRoot=loadUiBytes(Path(__file__).with_name("editor.ui").read_bytes()))
    source = QPixmap(640, 480)
    source.fill(Qt.lightGray)
    painter = QPainter(source)
    painter.fillRect(100, 90, 240, 180, Qt.darkGreen)
    painter.end()
    controller.showImages(source, source.copy(100, 90, 240, 180))
    return window


if __name__ == "__main__":
    app = QApplication.instance() or QApplication([])
    applyDesignerStyle(app)
    window = createWindow()
    window.show()
    app.exec_()
