"""Reproducible scene screenshots and interaction timings, not dot acceptance."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from statistics import median
import sys
from time import perf_counter
import importlib.util

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import emo_master  # noqa: E402,F401 - preload ONNX DLLs before Qt
from PySide2.QtCore import QPoint, QPointF, QRectF, Qt  # noqa: E402
from PySide2.QtGui import QColor, QFontDatabase, QImage, QPainter  # noqa: E402
from PySide2.QtWidgets import QApplication, QGraphicsView  # noqa: E402
from emo_master.apps.designer.ui.flow_scene import (  # noqa: E402
    FlowEdgeViewModel, FlowNodeViewModel, FlowScene,
)
from emo_master.apps.designer.ui.flow_routing import Route, simplify  # noqa: E402


def populate(scene, stress=False):
    count = 100 if stress else 9
    for i in range(count):
        scene.addFlowNode(FlowNodeViewModel(
            f"n{i}", f"Stage {i}", (i % 5) * 490, (i // 5) * 230,
            {f"in{j}": "number" for j in range(4)},
            {f"out{j}": "number" for j in range(2)},
        ))
    keys = []
    for i in range(count):
        for j in range(3 if stress else 2):
            target = (i + j + 1) % count
            key = (f"n{i}", "out0", f"n{target}", f"in{j}")
            scene.renderEdge(FlowEdgeViewModel(*key))
            keys.append(key)
    # Fixed, deliberately dense geometry isolates presentation from A* cost.
    # All endpoints use the real measured Qt ports and remain deterministic.
    for key in keys:
        item = scene._edgeItems[key]
        a = item.sourcePort.sceneBoundingRect().center()
        b = item.targetPort.sceneBoundingRect().center()
        channel = a.x() + 45
        item.applyRoute(Route(simplify(((a.x(), a.y()), (channel, a.y()),
                                       (channel, b.y()), (b.x(), b.y())))))
    scene._routeTimer.stop()
    scene._routesDirty = False
    return keys


def capture(scene, path, bounds):
    image = QImage(1500, 500, QImage.Format_ARGB32)
    image.fill(QColor("white"))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)
    scene.render(painter, QRectF(image.rect()), bounds)
    painter.end()
    if not image.save(str(path)):
        raise RuntimeError(f"Cannot save {path}")


def measure(scene, app, keys):
    durations = []
    for i in range(35):
        start = perf_counter()
        scene.clearSelection()
        scene.setEdgeSelected(keys[i % len(keys)], True)
        app.processEvents()
        elapsed = (perf_counter() - start) * 1000
        if i >= 5:
            durations.append(elapsed)
    scene.clearSelection()
    return statistics(durations)


def statistics(durations):
    return {"samples": len(durations), "medianMs": median(durations),
            "p95Ms": sorted(durations)[int(.95 * (len(durations) - 1))]}


def measuredView(scene, app, keys, output, name):
    view = QGraphicsView(scene)
    view.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
    view.setWindowTitle("Flow edge readability - developer validation")
    view.resize(1500, 800)
    view.setSceneRect(QRectF(-50, -50, 2600, 1300))
    view.fitInView(QRectF(-50, -50, 2600, 1300), Qt.KeepAspectRatio)
    view.show()
    app.processEvents()
    timings = []
    for i in range(35):
        start = perf_counter()
        scene.clearSelection()
        scene.setEdgeSelected(keys[i % len(keys)], True)
        view.viewport().repaint()
        app.processEvents()
        if i >= 5:
            timings.append((perf_counter() - start) * 1000)
    scene.clearSelection()
    view.viewport().repaint()
    view.grab().save(str(output / f"{name}-view.png"))
    shared = getattr(scene, "_sharedWireItems", [])
    if shared:
        scene._showEdgeMenu(shared[0].owners, view.mapToGlobal(QPoint(120, 120)))
        app.processEvents()
        scene._branchMenu.grab().save(str(output / f"{name}-branch-menu.png"))
        scene._closeEdgeMenu()
    node = scene._nodeItems[keys[0][0]]
    original = node.pos()
    dragTimings = []
    node.grabMouse()
    for i in range(35):
        start = perf_counter()
        node.setPos(original + QPointF(0, 2 if i % 2 else -2))
        view.viewport().repaint()
        app.processEvents()
        if i >= 5:
            dragTimings.append((perf_counter() - start) * 1000)
    node.setPos(original)
    node.ungrabMouse()
    scene._routeTimer.stop()
    scene._routesDirty = False
    view.close()
    view.setScene(None)
    return {"focusAndPaint": statistics(timings), "localDragAndPaint": statistics(dragTimings)}


def projectScene(scene, project, workflow):
    payload = json.loads(project.read_text(encoding="utf-8"))
    graph = payload["workflows"][workflow]
    positions = graph.get("layout", {}).get("nodePositions", {})
    for node in graph["nodes"]:
        position = positions.get(node["nodeId"], {"x": 0, "y": 0})
        scene.addFlowNode(FlowNodeViewModel(node["nodeId"], node.get("displayName", node["nodeId"]),
            position["x"], position["y"], node.get("inputPorts", {}), node.get("outputPorts", {}),
            node.get("operatorId", ""), node.get("kind", "operator")))
    keys = []
    for edge in graph["edges"]:
        key = tuple(edge[name] for name in ("fromNode", "fromPort", "toNode", "toPort"))
        scene.renderEdge(FlowEdgeViewModel(*key))
        keys.append(key)
    scene.refreshAllRoutes()
    return keys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--baseline-source", type=Path)
    parser.add_argument("--project", type=Path)
    parser.add_argument("--workflow", default="fanout")
    parser.add_argument("--visible", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    if os.name == "nt":
        fontId = QFontDatabase.addApplicationFont("C:/Windows/Fonts/msyh.ttc")
        QFontDatabase.addApplicationFont("C:/Windows/Fonts/segoeui.ttf")
        families = QFontDatabase.applicationFontFamilies(fontId)
        if families:
            app._designerFontFamily = families[0]
    sceneClass = FlowScene
    if args.baseline_source:
        spec = importlib.util.spec_from_file_location("flow_scene_baseline", args.baseline_source)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        sceneClass = module.FlowScene
    results = {"python": sys.executable, "platform": app.platformName(),
               "qpaEnvironment": os.environ.get("QT_QPA_PLATFORM"),
               "evidenceType": "automated-scene-render-not-independent-desktop"}
    for stress in (False, True):
        scene = sceneClass()
        keys = populate(scene, stress)
        name = "stress" if stress else "dense"
        results[name] = measure(scene, app, keys)
        if args.visible:
            results[name]["visiblePaint"] = measuredView(scene, app, keys, args.output, name)
        if not stress:
            bounds = QRectF(-30, -30, 2510, 800)
            capture(scene, args.output / "overview.png", bounds)
            scene.setNodeSelected("n2")
            capture(scene, args.output / "node-focus.png", bounds)
            scene.clearSelection()
            scene.setEdgeSelected(keys[0], True)
            capture(scene, args.output / "edge-focus.png", bounds)
        scene.clearGraph()
        scene.deleteLater()
        app.processEvents()
    if args.project:
        scene = sceneClass()
        keys = projectScene(scene, args.project, args.workflow)
        results["project"] = {"path": str(args.project.resolve()), "workflow": args.workflow,
                              "nodes": len(scene._nodeItems), "edges": len(keys),
                              "focus": measure(scene, app, keys)}
        if hasattr(scene, "_wireGeometry"):
            parts = scene._wireGeometry.parts
            results["project"]["geometry"] = {"paintedParts": len(parts),
                "sharedParts": sum(len(part.owners) > 1 for part in parts),
                "duplicatePartsAvoided": sum(len(part.owners) - 1 for part in parts),
                "junctions": len(scene._wireGeometry.junctions)}
        bounds = QRectF(*scene.getContentBounds())
        capture(scene, args.output / "project-overview.png", bounds)
        scene.setNodeSelected(keys[0][0])
        capture(scene, args.output / "project-node-focus.png", bounds)
        scene.clearSelection()
        scene.setEdgeSelected(keys[2], True)
        capture(scene, args.output / "project-edge-focus.png", bounds)
        if args.visible:
            results["project"]["visiblePaint"] = measuredView(scene, app, keys, args.output, "project")
        scene.clearGraph()
        scene.deleteLater()
        app.processEvents()
    (args.output / "timings.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
