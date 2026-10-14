"""Qt interaction checks for real-operation admission and debug-only SQL inputs."""
from contextlib import closing
import os
from pathlib import Path
import sqlite3

from PySide2.QtCore import Qt
from PySide2.QtTest import QTest
from PySide2.QtWidgets import QMessageBox, QVBoxLayout, QWidget
from shiboken2 import isValid

from emo_master.apps.designer.ui.operator_debug_window import OperatorDebugWindow
from emo_master.apps.designer.operator_editors.workspace_window import OperatorWorkspaceWindow
from emo_master.plugins.builtins.sqlite_writer.editor import SqliteWriterEditorController
from tests.designer.test_operator_debug_window import editorFor, wait
from tests.runtime.test_operator_debug_resources import sqliteConfig, uploadImage
from tests.runtime.test_operator_debug_rpc import runtime as runtime


def evidence(dialog, name):
    directory = os.environ.get("EMO_DEBUG_EVIDENCE_DIR")
    if directory:
        root = Path(directory)
        root.mkdir(parents=True, exist_ok=True)
        assert dialog.grab().save(str(root / (name + ".png")))


def testFileSaveDialogDefaultsToNoAndRequiresConfirmationEachTime(runtime, tmp_path, monkeypatch):
    editor, payload, before, calls = editorFor(runtime, "vision.io.image_saver")
    path = tmp_path / "output.png"
    operator = runtime.pluginScanResult.activeOperators["vision.io.image_saver"].operatorClass
    editor._schemaForm.setSchema(operator.meta.paramSchema, {"outputPath": str(path), "overwrite": True})
    dialog = OperatorDebugWindow(editor)
    editor._debugWindow = dialog
    dialog.show()
    answers = [QMessageBox.No, QMessageBox.Yes, QMessageBox.No]
    confirmations = []
    def confirm(*args):
        confirmations.append(args)
        assert args[-1] == QMessageBox.No
        return answers.pop(0)
    monkeypatch.setattr(QMessageBox, "warning", confirm)
    try:
        wait(lambda: dialog.run.isEnabled())
        dialog.rows["image"].setReference(uploadImage(dialog.connection), "fixture")
        QTest.mouseClick(dialog.run, Qt.LeftButton)
        assert not dialog.executionId and not path.exists() and not dialog.records
        QTest.mouseClick(dialog.run, Qt.LeftButton)
        wait(lambda: len(dialog.records) == 1 and dialog.run.isEnabled())
        assert dialog.records[0]["status"] == "SUCCEEDED" and path.is_file()
        QTest.mouseClick(dialog.run, Qt.LeftButton)
        assert len(dialog.records) == 1 and len(confirmations) == 3
        assert payload == before and calls == []
        evidence(dialog, "image-save-confirmed")
        dialog.close()
        wait(lambda: not isValid(dialog))
    finally:
        editor.forceClose()


def testSqliteEditorShowsMappedDebugInputsAndNeverWritesProduction(runtime, tmp_path):
    path = tmp_path / "production.sqlite3"
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("CREATE TABLE records(value INTEGER NOT NULL)")
    beforeDb = path.read_bytes()
    editor, payload, before, calls = editorFor(runtime, "vision.io.sqlite_writer")
    operator = runtime.pluginScanResult.activeOperators["vision.io.sqlite_writer"].operatorClass
    context = editor.context
    editor.forceClose()
    root = QWidget()
    QVBoxLayout(root)
    editor = OperatorWorkspaceWindow(key=context.key, title="SQLite 调试", context=context,
        schema=operator.meta.paramSchema, values=sqliteConfig(path), customRoot=root,
        controller=SqliteWriterEditorController())
    editor.show()
    dialog = OperatorDebugWindow(editor)
    editor._debugWindow = dialog
    dialog.show()
    try:
        wait(lambda: dialog.run.isEnabled())
        assert "mapping_1" in dialog.rows, editor.collectParams()
        dialog.rows["mapping_1"].restoreWire("value", {"inline": 19})
        QTest.mouseClick(dialog.run, Qt.LeftButton)
        wait(lambda: len(dialog.records) == 1 and dialog.run.isEnabled())
        assert dialog.records[0]["status"] == "SUCCEEDED", dialog.records
        assert dialog.records[0]["outputs"]["receipt"]["status"] == "COMMITTED"
        assert path.read_bytes() == beforeDb and payload == before and calls == []
        evidence(dialog, "sqlite-isolated")
        dialog.close()
        wait(lambda: not isValid(dialog))
    finally:
        editor.forceClose()
