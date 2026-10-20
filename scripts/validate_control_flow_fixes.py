"""Native production-form checks in isolated windows; no Runtime or device calls."""
from copy import deepcopy
from math import ceil
from pathlib import Path
from types import SimpleNamespace
import argparse
import json
import os
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--platform", choices=["windows", "offscreen"], default="windows")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    os.environ["QT_QPA_PLATFORM"] = args.platform
    import emo_master  # noqa: F401 - preload Windows DLLs before Qt
    from PySide2.QtCore import QCoreApplication, QEvent, QEventLoop, QSettings, QTimer, Qt
    from PySide2.QtGui import QFontDatabase, QGuiApplication, QTextDocument, QTextOption
    from PySide2.QtWidgets import QApplication, QLabel, QLineEdit
    from emo_master.apps.designer.main import _configureHighDpi, applyDesignerStyle
    from emo_master.apps.designer.operator_editors.controller_protocol import EditorKey
    from emo_master.apps.designer.operator_editors.workspace_window import OperatorWorkspaceWindow
    from emo_master.apps.designer.ui.main_window import MainWindow
    from emo_master.plugins.builtins.flow_switch.operator import FlowSwitchOperator

    _configureHighDpi(QCoreApplication, Qt, QGuiApplication)
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(args.output / "settings"))
    app = QApplication([])
    QFontDatabase.addApplicationFont("C:/Windows/Fonts/msyh.ttc")
    applyDesignerStyle(app)

    def settle():
        loop = QEventLoop()
        QTimer.singleShot(80, loop.quit)
        loop.exec_()

    body = SimpleNamespace(name="整数循环体", inputs={"item": "integer", "index": "integer"}, outputs={})
    source = SimpleNamespace(activeWorkflowId="main", workflowStore=SimpleNamespace(workflows={"body": body}),
                             _operatorDefinition=lambda _: {"paramSchema": FlowSwitchOperator.meta.paramSchema})
    report = {"platform": args.platform, "scale": os.getenv("QT_SCALE_FACTOR", "1"),
              "font": app.font().family(), "runtimeCalls": 0, "cases": []}
    for case in ("switch", "foreach-conflict"):
        if case == "switch":
            savedSchema = deepcopy(FlowSwitchOperator.meta.paramSchema)
            for field in savedSchema["properties"].values():
                field.pop("xOptionalPresence")
            node = SimpleNamespace(kind="operator", operatorId="vision.flow.switch", paramSchema=savedSchema)
            values = {"case0Value": "合格", "case1Value": ""}
        else:
            values = {"mode": "foreach", "bodyWorkflowId": "body", "itemInputPort": "item",
                      "indexInputPort": "item", "maxIterations": 10, "timeoutMs": 0}
            node = SimpleNamespace(kind="loop", loop=values)
        schema = MainWindow._nodeEditorSchema(source, node)
        applied = []
        key = EditorKey("validation", "main", case)
        context = SimpleNamespace(key=key, operatorId=case, paramSchema=schema, workflowOptions=["body"],
                                  bindWindowHooks=lambda *_: None,
                                  applyParams=lambda params: applied.append(deepcopy(params)) or True)
        editor = OperatorWorkspaceWindow(key=key, title="Switch 分支匹配" if case == "switch" else "ForEach 端口映射",
                                         context=context, schema=schema, values=values)
        form = editor._schemaForm
        if case == "switch":
            assert editor.collectParams() == values
            assert editor.applyChanges()
            assert applied == [values]
            empty = form._controls["case1Value"]
            absent = form._controls["case2Value"]
            assert empty.enabledCheckBox.isChecked() and not absent.enabledCheckBox.isChecked()
            absent.enabledCheckBox.setChecked(True)
            assert editor.collectParams()["case2Value"] == ""
            absent.enabledCheckBox.setChecked(False)
            assert "case2Value" not in editor.collectParams()
        else:
            assert not editor.applyChanges()
            assert applied == []
            assert "不能相同" in editor._statusLabel.text()
        for width in (460, 640):
            editor.resize(width, 500)
            editor.show()
            settle()
            for label in editor.findChildren(QLabel):
                if not label.isVisible() or not label.text():
                    continue
                if label.wordWrap():
                    doc = QTextDocument()
                    doc.setDocumentMargin(0)
                    doc.setDefaultFont(label.font())
                    option = QTextOption()
                    option.setWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
                    doc.setDefaultTextOption(option)
                    doc.setPlainText(label.text())
                    doc.setTextWidth(max(1, label.contentsRect().width()))
                    assert ceil(doc.size().height()) <= label.contentsRect().height() + 2, label.text()
                assert not label.grab().toImage().isNull()
            for name, control in form._controls.items():
                label = form._layout.labelForField(control)
                if label.isVisible() and control.isVisible():
                    assert form.rect().contains(control.geometry())
                    assert not label.geometry().intersects(control.geometry())
                if hasattr(control, "enabledCheckBox"):
                    assert control.rect().contains(control.enabledCheckBox.geometry())
                    assert control.rect().contains(control.valueControl.geometry())
                    assert not control.enabledCheckBox.geometry().intersects(control.valueControl.geometry())
                    assert control.enabledCheckBox.fontMetrics().horizontalAdvance("启用") + 24 <= control.enabledCheckBox.width()
                    assert isinstance(control.valueControl, QLineEdit)
            raster = editor.grab().toImage()
            assert len({raster.pixel(x, y) for y in range(0, raster.height(), 3)
                        for x in range(0, raster.width(), 3)}) > 16
            assert raster.save(str(args.output / f"{case}-{width}.png"))
            report["cases"].append({"case": case, "requestedWidth": width, "actualWidth": editor.width(), "result": "PASS"})
        editor.forceClose()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    (args.output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
