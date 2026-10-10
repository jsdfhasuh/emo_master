"""Capture the interface form in isolated Qt windows, without Runtime calls."""
from pathlib import Path
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
    import emo_master  # noqa: F401 - preload native DLL paths before Qt
    from PySide2.QtCore import QCoreApplication, QEvent, QEventLoop, QTimer, Qt
    from PySide2.QtGui import QFontDatabase, QGuiApplication
    from PySide2.QtWidgets import QApplication, QLabel
    from emo_master.apps.designer.main import _configureHighDpi, applyDesignerStyle
    from emo_master.apps.designer.ui.workflow_interface_dialog import (
        WorkflowInterfaceDialog, _PortPropertiesDialog,
    )

    _configureHighDpi(QCoreApplication, Qt, QGuiApplication)
    app = QApplication([])
    # Offscreen Windows may otherwise silently fall back to a font with no CJK.
    fontPath = Path(os.getenv("WINDIR", "C:/Windows")) / "Fonts/msyh.ttc"
    if fontPath.is_file():
        QFontDatabase.addApplicationFont(str(fontPath))
    applyDesignerStyle(app)
    fontProbe = QLabel("工作流接口名称数据类型确认配置")
    fontProbe.resize(480, 50)
    fontProbe.show()
    app.processEvents()
    raster = fontProbe.grab().toImage()
    assert len({raster.pixel(x, y) for y in range(0, raster.height(), 2)
                for x in range(0, raster.width(), 2)}) > 2, "text rasterization failed"
    fontProbe.close()
    fontProbe.deleteLater()

    report = {"platform": args.platform, "scale": os.getenv("QT_SCALE_FACTOR", "1"),
              "runtimeCalls": 0, "font": app.font().family(), "cases": []}
    cases = [
        ("inputs", {"hasNext": "boolean"}, {"continue": "boolean"}, "inputs"),
        ("outputs", {"hasNext": "boolean"}, {"continue": "boolean"}, "outputs"),
        ("empty", {}, {}, "inputs"),
        ("many", {f"value{index}": "integer" for index in range(18)}, {}, "inputs"),
        ("validation", {"hasNext": "boolean"}, {}, "inputs"),
    ]
    for name, inputs, outputs, initialTab in cases:
        dialog = WorkflowInterfaceDialog("Continue While Images Remain", inputs, outputs, initialTab=initialTab)
        try:
            if name == "validation":
                dialog._inputTable.addPort("hasNext", "integer", focus=False)
            for width, height in ((700, 480), (640, 430)):
                dialog.resize(width, height)
                dialog.show()
                loop = QEventLoop()
                QTimer.singleShot(80, loop.quit)
                loop.exec_()
                table = dialog._outputTable if initialTab == "outputs" else dialog._inputTable
                assert dialog.width() <= width and dialog.height() <= height
                clipped = []
                for label in dialog.findChildren(QLabel):
                    if label.isVisible() and label.wordWrap() and label.heightForWidth(label.width()) > label.height() + 2:
                        clipped.append(label.text())
                for control in (table._addButton, dialog._saveButton, dialog._cancelButton):
                    assert dialog.rect().contains(control.mapTo(dialog, control.rect().bottomRight()))
                if table._rows:
                    assert table._table.viewport().height() > 70
                    for column in range(4):
                        widget = table._table.cellWidget(0, column)
                        assert widget.width() > 45
                        assert widget.height() >= widget.sizeHint().height()
                if name == "many":
                    assert table._table.verticalScrollBar().maximum() > 0
                assert dialog._saveButton.isEnabled() == (name != "validation")
                screenshot = args.output / f"{name}-{width}.png"
                assert dialog.grab().save(str(screenshot))
                report["cases"].append({"case": name, "width": dialog.width(), "height": dialog.height(),
                    "devicePixelRatio": dialog.devicePixelRatioF(), "clippedLabels": clipped,
                    "screenshot": screenshot.name})
        finally:
            dialog.close()
            dialog.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    for name, side, typeName in (
        ("properties-inputs", "输入", "list<detectionCollection>"),
        ("properties-outputs", "输出", "detectionCollection"),
        ("properties-validation", "输入", "string"),
    ):
        spec = {"type": "detectionCollection", "required": False, "nullable": True, "schemaVersion": "1.x"}
        dialog = _PortPropertiesDialog(side, "检测结果", typeName, spec)
        try:
            for width, height in ((520, 360), (440, 330)):
                dialog.resize(width, height)
                dialog.show()
                loop = QEventLoop()
                QTimer.singleShot(80, loop.quit)
                loop.exec_()
                assert dialog.width() <= width and dialog.height() <= height
                clipped = []
                for label in dialog.findChildren(QLabel):
                    if label.isVisible() and label.wordWrap() and label.heightForWidth(label.width()) > label.height() + 2:
                        clipped.append(label.text())
                for control in (dialog._requiredCombo, dialog._nullableCombo, dialog._versionCheck,
                                dialog._versionEdit, dialog._saveButton, dialog._cancelButton):
                    assert dialog.rect().contains(control.mapTo(dialog, control.rect().bottomRight()))
                    assert control.height() >= control.sizeHint().height()
                assert dialog._saveButton.isEnabled() == (name != "properties-validation")
                screenshot = args.output / f"{name}-{width}.png"
                assert dialog.grab().save(str(screenshot))
                report["cases"].append({"case": name, "width": dialog.width(), "height": dialog.height(),
                    "devicePixelRatio": dialog.devicePixelRatioF(), "clippedLabels": clipped,
                    "screenshot": screenshot.name})
        finally:
            dialog.close()
            dialog.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    report["status"] = "PASS" if not any(case["clippedLabels"] for case in report["cases"]) else "FAIL"
    (args.output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=True))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
