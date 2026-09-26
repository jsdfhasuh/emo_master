"""Capture icon consumers at real Qt DPI settings without running hardware."""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--scale", type=float, default=1.0)
    args = parser.parse_args()
    os.environ["QT_QPA_PLATFORM"] = "windows" if sys.platform == "win32" else "offscreen"
    os.environ["QT_SCALE_FACTOR"] = str(args.scale)
    os.environ["QT_SCREEN_SCALE_FACTORS"] = "1"
    os.environ["QT_FONT_DPI"] = "96"
    os.environ["HUARAY_CAMERA_SMOKE"] = "0"
    from emo_master.apps.designer.main import applyDesignerStyle, configureHighDpi
    configureHighDpi()
    from PySide2.QtCore import QRectF, Qt
    from PySide2.QtGui import QImage, QPainter
    from PySide2.QtWidgets import QApplication
    from emo_master.apps.designer.services.runtime_client import RuntimeClient
    from emo_master.apps.designer.ui.main_window import MainWindow
    from emo_master.apps.runtime.grpc_server.service import RuntimeService

    class Settings:
        def value(self, _key, default=None):
            return default

        def setValue(self, *_args):
            pass

    app = QApplication([])
    applyDesignerStyle(app)

    def waitFor(predicate):
        deadline = time.monotonic() + 8
        while not predicate():
            if time.monotonic() >= deadline:
                raise RuntimeError("custom icon rendering did not complete")
            app.processEvents()
            time.sleep(0.005)

    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    report = {"scale": args.scale, "platform": app.platformName(), "consumers": []}
    with TemporaryDirectory(prefix="emo-icon-visual-") as temporary:
        service = RuntimeService(dbPath=Path(temporary) / "runtime.db")
        client = RuntimeClient(service, ownedRuntimeService=service)
        window = MainWindow(client, settingsStore=Settings())
        try:
            window.setAttribute(Qt.WA_DontShowOnScreen, True)
            window.show()
            waitFor(lambda: window.operatorCatalogController.hasCatalog)
            window.resize(1400, 850)
            window.expandSidebar()
            assert math.isclose(window.devicePixelRatioF(), args.scale, abs_tol=0.01)
            for index, item in enumerate(window.flowScene._nodeItems.values()):
                item.setPos(20 + index * 600, 320)
            ids = ("vision.edge.canny", "vision.io.huaray_camera", "vision.inference.yolo")
            definitions = [next(item for item in window.operatorCatalog if item["operatorId"] == name) for name in ids]
            nodes = []
            for index, definition in enumerate(definitions):
                window.addNodeFromOperatorPayload(definition, sceneX=20 + index * 300, sceneY=40)
                nodes.append(next(node for node in window.flowModel.nodes.values() if node.operatorId == definition["operatorId"]))
            items = [window.flowScene._nodeItems[node.nodeId] for node in nodes]
            for item, state in zip(items, ("COMPLETED", "RUNNING", "FAILED")):
                item.setRuntimeState(state)
            window.flowScene.setNodeSelected(nodes[-1].nodeId)
            window.onNodeSelectionChanged()
            targets = items + window._sidebarIconItems + [window.nodeDetailIcon]
            waitFor(lambda: all(getattr(item, "_operatorIconSource", "") == "custom" for item in targets))
            window.focusGraphContent()
            window.flowView.fitInView(window.flowScene.itemsBoundingRect().adjusted(-30, -30, 30, 30), Qt.KeepAspectRatio)
            allItems = list(window.flowScene._nodeItems.values())
            assert not any(first.sceneBoundingRect().intersects(second.sceneBoundingRect())
                           for index, first in enumerate(allItems) for second in allItems[index + 1:])
            app.processEvents()
            assert window.grab().save(str(output / "main.png"))
            sceneRect = window.flowScene.itemsBoundingRect().adjusted(-10, -10, 10, 10)
            image = QImage(int(sceneRect.width() * args.scale), int(sceneRect.height() * args.scale), QImage.Format_ARGB32)
            image.setDevicePixelRatio(args.scale)
            image.fill(Qt.white)
            painter = QPainter(image)
            window.flowScene.render(painter, target=QRectF(0, 0, sceneRect.width(), sceneRect.height()), source=sceneRect)
            painter.end()
            assert image.save(str(output / "canvas.png"))
            bubble = window.operatorBubble
            bubble.setAttribute(Qt.WA_DontShowOnScreen, True)
            bubble.setOperators(definitions)
            bubble.show()
            waitFor(lambda: all(getattr(button, "_operatorIconSource", "") == "custom" for button in bubble._buttons))
            assert bubble.grab().save(str(output / "cards.png"))
            for target in targets + bubble._buttons:
                report["consumers"].append({"type": type(target).__name__,
                    "renderSource": target._operatorIconSource, "sha256": target._operatorIconSha})
            report["windowSize"] = [window.width(), window.height()]
            report["devicePixelRatio"] = window.devicePixelRatioF()
            report["nodeGeometry"] = [{"width": item.rect().width(), "height": item.rect().height(),
                                        "title": item.model.title, "state": item.getVisualStyle()["runtimeState"]}
                                       for item in items]
            assert len(report["consumers"]) == 10
        finally:
            report["workersStopped"] = window.shutdownOperatorDisplay()
            window.close()
            client.close()
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
