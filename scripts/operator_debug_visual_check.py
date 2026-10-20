"""Isolated native Qt render/click acceptance, without touching a live Designer."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import tempfile
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--scale", type=float, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    os.environ["QT_QPA_PLATFORM"] = "windows"
    os.environ["QT_SCALE_FACTOR"] = str(args.scale)
    os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "1"
    import emo_master  # noqa: F401
    from PySide2.QtCore import Qt
    from PySide2.QtGui import QFont, QFontDatabase
    from PySide2.QtTest import QTest
    from PySide2.QtWidgets import QApplication
    from shiboken2 import isValid
    from emo_master.apps.runtime.grpc_server.service import RuntimeService
    from tests.designer.test_operator_debug_window import editorFor
    from emo_master.apps.designer.ui.operator_debug_window import OperatorDebugWindow

    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling)
    app = QApplication([])
    from emo_master.apps.designer.main import applyDesignerStyle
    applyDesignerStyle(app)
    fontId = QFontDatabase.addApplicationFont("C:/Windows/Fonts/msyh.ttc")
    families = QFontDatabase.applicationFontFamilies(fontId)
    assert families, "Chinese font unavailable"
    app.setFont(QFont(families[0], 10))
    args.output.mkdir(parents=True, exist_ok=True)
    records = []
    def wait(predicate):
        deadline = time.monotonic()+15
        while not predicate() and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.01)
        assert predicate()
    with tempfile.TemporaryDirectory(prefix="emo-debug-visual-") as root:
        os.environ["EMO_RUNTIME_DATA_DIR"] = root
        service = RuntimeService(dbPath=Path(root)/"runtime.db", workspaceRoot=Path(root)/"jobs")
        editor, *_ = editorFor(service, "vision.compare.number")
        editor.collectParams = lambda: {"operator": "gte", "rightValue": 3}
        dialog = OperatorDebugWindow(editor)
        editor._debugWindow = dialog
        dialog.show()
        try:
            wait(lambda: dialog.run.isEnabled())
            dialog.rows["left"].mode.setCurrentIndex(1)
            dialog.rows["left"].text.setText("4")
            QTest.mouseClick(dialog.run, Qt.LeftButton)
            wait(lambda: bool(dialog.records) and dialog.run.isEnabled())
            assert dialog.records[0]["outputs"] == {"result": True}
            for width, height in [(1280, 720), (1600, 900), (1920, 1080)]:
                logical = (int(width/args.scale), int(height/args.scale))
                dialog.resize(*logical)
                dialog.status.setText("就绪 | 当前输入已冻结 | 未保存参数的本次调试不改变工程配置")
                for index in [0, 1, 2, 3]:
                    dialog.tabs.setCurrentIndex(index)
                    for _ in range(8):
                        app.processEvents()
                    dialog.resize(*logical)
                    app.processEvents()
                    assert dialog.width() <= logical[0] and dialog.height() <= logical[1], (logical, dialog.size(), dialog.minimumSizeHint(), index)
                    for control in [dialog.run, dialog.end, dialog.status, dialog.tabs]:
                        assert dialog.rect().contains(control.geometry())
                    bounds = dialog.status.fontMetrics().boundingRect(dialog.status.contentsRect(), Qt.TextWordWrap, dialog.status.text())
                    assert bounds.height() <= dialog.status.height()
                    pixmap = dialog.grab()
                    filename = f"{width}x{height}-scale{args.scale:g}-tab{index}.png"
                    assert pixmap.save(str(args.output / filename))
                    image = dialog.status.grab().toImage()
                    pixels = {image.pixel(x, y) for y in range(image.height()) for x in range(image.width())}
                    assert len(pixels) > 8, "blank or missing text pixels"
                    records.append(dict(file=filename, logical=logical, actual=[dialog.width(), dialog.height()],
                                        textHeight=bounds.height(), rowHeight=dialog.status.height(), colors=len(pixels)))
            dialog.close()
            wait(lambda: not isValid(dialog))
            assert not service.operatorDebugManager.ownsResources()
        finally:
            editor.forceClose()
            service.close()
    (args.output / f"scale{args.scale:g}.json").write_text(json.dumps(records, indent=2), encoding="utf-8")
    print(f"PASS: {len(records)} native screenshots, text-pixel and click checks at scale {args.scale}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
