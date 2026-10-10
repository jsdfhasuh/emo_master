"""Capture parameter labels using native Qt and inert editor contexts."""
from pathlib import Path
import argparse
import importlib
import json
import os
import sys
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--platform", choices=["windows", "offscreen"], default="windows")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    os.environ["QT_QPA_PLATFORM"] = args.platform
    import emo_master  # noqa: F401 - initialize Windows dependency DLL paths
    from PySide2.QtCore import QCoreApplication, QEvent, QEventLoop, QTimer, Qt
    from PySide2.QtGui import QGuiApplication
    from PySide2.QtWidgets import QApplication, QLabel, QToolTip
    from emo_master.apps.designer.main import _configureHighDpi, applyDesignerStyle
    from emo_master.apps.designer.operator_editors.ui_loader import loadUiBytes
    from emo_master.apps.designer.operator_editors.controller_protocol import EditorKey
    from emo_master.apps.designer.operator_editors.workspace_window import OperatorWorkspaceWindow
    from emo_master.apps.designer.state.schema_utils import applySchemaDefaults

    _configureHighDpi(QCoreApplication, Qt, QGuiApplication)
    app = QApplication([])
    applyDesignerStyle(app)

    def settle():
        loop = QEventLoop()
        QTimer.singleShot(60, loop.quit)
        loop.exec_()

    report = {"platform": args.platform, "scale": os.getenv("QT_SCALE_FACTOR", "1"),
              "hardwareCalls": 0, "cases": []}
    cases = ["blur", "roi", "histogram", "huaray_camera", "plc_slmp_read", "sqlite_writer", "nested"]
    for case in cases:
        controller = None
        content = None
        operatorId = case
        if case == "nested":
            schema = {"properties": {"region": {"type": "object", "title": "区域配置", "properties": {
                "timeout": {"type": "integer", "title": "相机采集触发后等待图像返回的最长时间（毫秒）"},
                "enabled": {"type": "boolean", "title": "启用", "default": True},
            }}}}
        else:
            directory = ROOT / "src/emo_master/plugins/builtins" / case
            manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
            schema = manifest["paramSchema"]
            operatorId = manifest["operatorId"]
            editor = manifest.get("editor")
            if editor:
                content = loadUiBytes((directory / editor["uiResource"]).read_bytes())
                module, name = editor["controllerEntry"].split(":")
                controller = getattr(importlib.import_module(module), name)()
        key = EditorKey("validation", "main", case)
        context = SimpleNamespace(
            key=key, paramSchema=schema, workflowOptions=[], operatorId=operatorId,
            sqliteLocation=lambda: ("validation-only", False),
            setStatus=lambda *_: None, setError=lambda *_: None, markDirty=lambda: None,
            log=lambda *_: None, bindWindowHooks=lambda *_: None,
        )
        window = OperatorWorkspaceWindow(
            key=key, title=case, context=context, schema=schema,
            values=applySchemaDefaults(schema, {}), customRoot=content, controller=controller,
        )
        content = content or window._schemaForm
        try:
            for width in (640, 1100):
                screen = app.primaryScreen().availableGeometry()
                window.resize(min(width, screen.width() - 40), min(700, screen.height() - 80))
                window.show()
                settle()
                labels = [label for label in content.findChildren(QLabel)
                          if "参数键：" in label.toolTip() and label.isVisible()]
                assert labels, case
                clipped = []
                for label in labels:
                    # Wrapped labels must have enough height for their actual width.
                    requiredHeight = label.heightForWidth(label.width())
                    if requiredHeight > label.height() + 2:
                        clipped.append(label.text())
                shot = args.output / f"{case}-{width}.png"
                assert window.grab().save(str(shot))
                report["cases"].append({"case": case, "requestedWidth": width,
                    "width": window.width(), "height": window.height(),
                    "devicePixelRatio": window.devicePixelRatioF(), "labels": len(labels),
                    "clippedLabels": clipped, "screenshot": shot.name})
                if case == "blur" and width == 1100:
                    label = labels[0]
                    QToolTip.showText(label.mapToGlobal(label.rect().center()), label.toolTip(), label)
                    settle()
                    for widget in app.topLevelWidgets():
                        if widget.isVisible() and widget.windowType() == Qt.ToolTip:
                            widget.grab().save(str(args.output / "tooltip.png"))
                    QToolTip.hideText()
            if case == "plc_slmp_read":
                controller.tabs.setCurrentWidget(controller.runtimePage)
                controller.advancedButton.setChecked(True)
                settle()
                assert controller.advancedScroll.viewport().height() > 0
                assert window.grab().save(str(args.output / "plc-advanced.png"))
        finally:
            window.forceClose()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    report["status"] = "PASS" if not any(case["clippedLabels"] for case in report["cases"]) else "FAIL"
    (args.output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=True))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
