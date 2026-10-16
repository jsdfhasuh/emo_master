"""Isolated real Qt widgets; no connection to the user's Designer or Runtime."""
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
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    import emo_master  # noqa: F401 - preload before Qt
    from PySide2.QtCore import QCoreApplication, QEventLoop, QSettings, QTimer, Qt
    from PySide2.QtGui import QFontDatabase, QGuiApplication
    from PySide2.QtWidgets import QApplication
    from emo_master.apps.designer.main import _configureHighDpi, applyDesignerStyle
    from emo_master.apps.designer.ui.global_variables_dialog import GlobalVariablesDialog, VariableDefinitionDialog
    from emo_master.apps.designer.operator_editors.controller_protocol import EditorContext, EditorKey
    from emo_master.apps.designer.operator_editors.workspace_window import OperatorWorkspaceWindow
    _configureHighDpi(QCoreApplication, Qt, QGuiApplication)
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(args.output / "settings"))
    app = QApplication([])
    QFontDatabase.addApplicationFont("C:/Windows/Fonts/msyh.ttc")
    applyDesignerStyle(app)

    def settle():
        loop = QEventLoop()
        QTimer.singleShot(120, loop.quit)
        loop.exec_()

    values = {
        "always": dict(name="alwaysTrue · 持续循环布尔常量", type="boolean", kind="constant", lifetime="persistent", initialValue=True),
        "enabled": dict(name="runEnabled · 工作流循环运行开关与长名称显示验证", type="boolean", kind="variable", lifetime="job", initialValue=True),
        "threshold": dict(name="检测阈值 / threshold", type="number", kind="variable", lifetime="persistent", initialValue=.5),
    }
    rows = [{"variableId": key, **value, "value": value["initialValue"], "revision": 1, "updatedAtMs": 1791500000000,
             "state": "current" if key != "enabled" else "initial"} for key, value in values.items()]
    client = SimpleNamespace(globalVariableState=lambda *args: {"variables": rows, "jobs": []})
    dialog = GlobalVariablesDialog(client, lambda: values, lambda _v: None, lambda: False, lambda: "")
    dialog.showForProject("isolated-ui-validation")
    settle()
    dialog.timer.stop()
    report = {"platform": app.platformName(), "scale": os.environ.get("QT_SCALE_FACTOR", "1"), "cases": []}
    def capture(widget, name, width):
        widget.resize(width, 560)
        widget.show()
        settle()
        image = widget.grab()
        image.save(str(args.output / f"{name}-{width}.png"))
        report["cases"].append(dict(name=name, requestedWidth=width, width=widget.width(), height=widget.height(),
                                    pixelWidth=image.width(), pixelHeight=image.height()))
    for width in (980, 640):
        capture(dialog, "management", width)
    definition = VariableDefinitionDialog(values["always"])
    capture(definition, "constant-definition", 460)
    assert not definition.type.isEnabled() and not definition.kind.isEnabled()
    definition.close()
    context = EditorContext(key=EditorKey("project", "main", "threshold"), operatorId="threshold", version="1", previewMode="none",
        paramSchema={"type": "object", "properties": {"threshold": {"type": "number", "title": "二值化检测阈值", "minimum": 0, "maximum": 1}}},
        runtimeClient=client, applyParams=lambda *args: True, appendLog=lambda *args: None,
        variableDefinitions=lambda: values, variableBindings=[{"parameterPath": ["threshold"], "variableId": "threshold"}],
        applyConfiguration=lambda *args: True)
    editor = OperatorWorkspaceWindow(key=context.key, title="算子参数 · 全局变量引用", context=context,
        schema=context.paramSchema, values={"threshold": .8})
    for width in (640, 460):
        capture(editor, "binding", width)
    assert editor.applyChanges()
    values["threshold"]["name"] = "重命名后的检测阈值 / renamedThreshold / 长名称验证"
    editor.refreshSchema(context.paramSchema)
    assert not editor.isDirty()
    assert editor._schemaForm._variableSources["threshold"].currentData() == "threshold"
    assert editor.collectParams() == {"threshold": .8}
    capture(editor, "binding-refreshed", 460)
    del values["threshold"]
    editor.refreshSchema(context.paramSchema)
    assert not editor.applyChanges()
    assert "失效" in editor._schemaForm._variableSources["threshold"].currentText()
    capture(editor, "binding-invalid", 460)
    editor.forceClose()
    dialog.shutdown()
    dialog.close()
    (args.output / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
