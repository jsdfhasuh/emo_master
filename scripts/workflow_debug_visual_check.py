"""Native Windows workflow debugger acceptance; never attaches to a user's app."""
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
    parser.add_argument("--long-identities", action="store_true")
    args = parser.parse_args()
    os.environ.update(QT_QPA_PLATFORM="windows", QT_SCALE_FACTOR=str(args.scale), QT_ENABLE_HIGHDPI_SCALING="1")
    import emo_master  # noqa: F401
    from PySide2.QtCore import Qt
    from PySide2.QtGui import QFont, QFontDatabase
    from PySide2.QtTest import QTest
    from PySide2.QtWidgets import QApplication
    from shiboken2 import isValid
    from emo_master.apps.designer.main import applyDesignerStyle
    from emo_master.apps.designer.services.runtime_client import RuntimeClient
    from emo_master.apps.designer.ui.workflow_debug_window import WorkflowDebugWindow
    from emo_master.apps.runtime.grpc_server.service import RuntimeService
    from tests.runtime.test_workflow_debug_control import nestedProject
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling)
    app = QApplication([])
    applyDesignerStyle(app)
    font = QFontDatabase.addApplicationFont("C:/Windows/Fonts/msyh.ttc")
    families = QFontDatabase.applicationFontFamilies(font)
    assert families
    app.setFont(QFont(families[0], 10))
    args.output.mkdir(parents=True, exist_ok=True)
    records = []
    def wait(predicate):
        until = time.monotonic() + 20
        while not predicate() and time.monotonic() < until:
            app.processEvents()
            time.sleep(.01)
        assert predicate()
    with tempfile.TemporaryDirectory(prefix="emo-flow-debug-visual-") as root:
        os.environ["EMO_RUNTIME_DATA_DIR"] = root
        service = RuntimeService(dbPath=Path(root)/"runtime.db", workspaceRoot=Path(root)/"jobs")
        payload, leaf = nestedProject(), "number"
        if args.long_identities:
            childId, leaf = "long_workflow_" + "W" * 90, "long_node_" + "W" * 90
            child = payload["workflows"].pop("child")
            payload["workflows"][childId] = child
            payload["workflowOrder"][1] = childId
            child["nodes"][1]["nodeId"] = leaf
            child["edges"][0]["fromNode"] = leaf
            for node in payload["workflows"]["main"]["nodes"]:
                if node.get("kind") == "subflow":
                    node["targetWorkflowId"] = childId
        dialog = WorkflowDebugWindow(RuntimeClient(service), payload, "main")
        dialog.show()
        try:
            wait(lambda: dialog.startButton.isEnabled())
            QTest.mouseClick(dialog.startButton, Qt.LeftButton)
            wait(lambda: dialog.current is not None and dialog.buttons["into"].isEnabled())
            QTest.mouseClick(dialog.buttons["into"], Qt.LeftButton)
            wait(lambda: dialog.current is not None and dialog.current["nodeId"] == leaf and dialog.buttons["out"].isEnabled())
            dialog.timer.stop()
            for width, height in ((1280, 720), (1600, 900), (1920, 1080)):
                logical = (int(width/args.scale), int(height/args.scale))
                for tab in range(dialog.tabs.count()):
                    dialog.tabs.setCurrentIndex(tab)
                    dialog.resize(*logical)
                    for _ in range(8):
                        app.processEvents()
                    dialog.resize(*logical)
                    app.processEvents()
                    assert dialog.width() <= logical[0] and dialog.height() <= logical[1], (logical, dialog.size(), tab)
                    for widget in (dialog.status, dialog.position, dialog.startButton, dialog.end, dialog.tabs):
                        assert dialog.rect().contains(widget.geometry()), (tab, widget.geometry())
                    textEvidence = []
                    for label in (dialog.status, dialog.position):
                        bounds = label.fontMetrics().boundingRect(label.contentsRect(), Qt.TextWordWrap, label.text())
                        assert bounds.height() <= label.height(), (label.text(), bounds, label.geometry())
                        assert bounds.width() <= label.width() + 2, (label.text(), bounds, label.geometry())
                        pixels = label.grab().toImage()
                        colors = {pixels.pixel(x, y) for y in range(pixels.height()) for x in range(pixels.width())}
                        assert len(colors) > 8
                        textEvidence.append(dict(textHeight=bounds.height(), rowHeight=label.height(), colors=len(colors)))
                    filename = f"{width}x{height}-scale{args.scale:g}-tab{tab}.png"
                    assert dialog.grab().save(str(args.output / filename))
                    records.append(dict(file=filename, logical=logical, actual=[dialog.width(), dialog.height()], text=textEvidence))
            dialog.timer.start(250)
            QTest.mouseClick(dialog.buttons["out"], Qt.LeftButton)
            wait(lambda: dialog.current is not None and dialog.current["phase"] == "call.return" and dialog.buttons["continue"].isEnabled())
            assert dialog.current["outputs"] == {"value": 7}
            dialog.close()
            wait(lambda: not isValid(dialog))
            assert not service.operatorDebugManager.ownsResources()
        finally:
            if isValid(dialog):
                dialog.close()
                wait(lambda: not isValid(dialog))
            service.close()
    (args.output / f"scale{args.scale:g}.json").write_text(json.dumps(records, indent=2), encoding="utf-8")
    print(f"PASS: {len(records)} native screenshots, confirmed stepping and text pixels at {args.scale}")


if __name__ == "__main__":
    main()
