"""Capture real Qt widgets using isolated settings and no hardware access."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


class MemorySettings:
    def __init__(self):
        self.values = {}

    def value(self, key, default=None):
        return self.values.get(key, default)

    def setValue(self, key, value):
        self.values[key] = value


class PreviewRuntime:
    def listOperators(self):
        return []

    def listNodePreviewSources(self, *_args):
        return []

    def getOperatorEditorAsset(self, operatorId, version):
        _ = version
        for directory in (ROOT / "src/emo_master/plugins/builtins").iterdir():
            manifestPath = directory / "manifest.json"
            if not manifestPath.is_file():
                continue
            manifest = json.loads(manifestPath.read_text(encoding="utf-8"))
            if manifest["operatorId"] == operatorId:
                content = (directory / manifest["editor"]["uiResource"]).read_bytes()
                return SimpleNamespace(ok=True, content=content,
                                       sha256=hashlib.sha256(content).hexdigest(), message="ok")
        raise ValueError(operatorId)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--scale", type=float, default=1.0)
    parser.add_argument("--width", type=int, default=1600)
    parser.add_argument("--height", type=int, default=900)
    parser.add_argument("--screen", type=int, default=0)
    parser.add_argument("--cross-screens", action="store_true")
    parser.add_argument("--project", type=Path, default=ROOT / "field_tests/camera_ui/project.json")
    args = parser.parse_args()
    os.environ["QT_QPA_PLATFORM"] = "windows" if sys.platform == "win32" else "offscreen"
    os.environ["QT_SCALE_FACTOR"] = str(args.scale)
    from emo_master.apps.designer.main import applyDesignerStyle, configureHighDpi
    configureHighDpi()
    from PySide2.QtCore import QEvent, QObject, QPoint, Qt
    from PySide2.QtGui import QColor, QFontInfo, QImage, QPainter
    from PySide2.QtWidgets import QApplication, QLabel, QWidget
    from emo_master.apps.designer.operator_editors import OperatorEditorManager
    from emo_master.apps.designer.ui.main_window import MainWindow
    from emo_master.apps.designer.ui.node_param_dialog import NodeParamDialog
    from emo_master.apps.designer.ui.project_entry_dialog import ProjectEntryDialog
    from emo_master.apps.designer.ui.global_counters_dialog import GlobalCountersDialog
    from emo_master.apps.designer.ui.log_dialog import LogDialog
    from emo_master.apps.designer.ui.flow_scene import FlowNodeViewModel, FlowEdgeViewModel
    from emo_master.apps.designer.state.workflow_package import (
        WorkflowPackageImportPreview, WorkflowPackageWorkflowPreview,
    )
    from emo_master.apps.designer.ui.workflow_package_preview_dialog import WorkflowPackagePreviewDialog

    class QuietWindows(QObject):
        def eventFilter(self, obj, event):
            if event.type() == QEvent.Polish and isinstance(obj, QWidget) and obj.isWindow():
                obj.setAttribute(Qt.WA_DontShowOnScreen, True)
                obj.setAttribute(Qt.WA_ShowWithoutActivating, True)
            return False

    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    app = QApplication([])
    quiet = QuietWindows(app)
    app.installEventFilter(quiet)
    applyDesignerStyle(app)
    screen = app.screens()[args.screen]
    runtime = PreviewRuntime()
    settings = MemorySettings()
    report = {"platform": app.platformName(), "scale": args.scale,
              "font": QFontInfo(app.font()).family(), "screen": screen.name(),
              "requested_window": [args.width, args.height],
              "screens": [{"name": item.name(), "dpr": item.devicePixelRatio(),
                           "logical_size": [item.size().width(), item.size().height()],
                           "logical_dpi": item.logicalDotsPerInch()} for item in app.screens()],
              "available_screen": [screen.availableGeometry().width(), screen.availableGeometry().height()],
              "windows": {}}

    def capture(name, widget, size=None, targetScreen=None):
        targetScreen = targetScreen or screen
        widget.setAttribute(Qt.WA_DontShowOnScreen, True)
        widget.winId()
        widget.windowHandle().setScreen(targetScreen)
        widget.move(targetScreen.availableGeometry().topLeft() + QPoint(20, 40))
        if size:
            widget.resize(*size)
        widget.show()
        for _ in range(8):
            app.processEvents()
        image = widget.grab()
        image.save(str(output / f"{name}.png"))
        actualScreen = widget.windowHandle().screen()
        clips = []
        for label in widget.findChildren(QLabel):
            text = QLabel.text(label)
            pixmap = label.pixmap()
            if not label.isVisibleTo(widget) or not text or (pixmap is not None and not pixmap.isNull()):
                continue
            if not label.wordWrap() and "\n" not in text and not text.startswith("<"):
                metrics = label.fontMetrics()
                if metrics.horizontalAdvance(text) > label.contentsRect().width() + 2:
                    clips.append({"name": label.objectName(), "text": text, "width": label.width()})
        report["windows"][name] = {
            "size": [widget.width(), widget.height()],
            "minimum": [widget.minimumSizeHint().width(), widget.minimumSizeHint().height()],
            "dpr": image.devicePixelRatio(), "pixels": [image.width(), image.height()],
            "text_clipping": clips,
            "screen": actualScreen.name(),
            "available": [actualScreen.availableGeometry().width(), actualScreen.availableGeometry().height()],
        }
        widget.hide()

    window = MainWindow(runtime, settingsStore=settings)
    if args.project.exists():
        raw = args.project.read_bytes()
        report["project_sha256"] = hashlib.sha256(raw).hexdigest()
        window.workflowStore.loadProjectPayload(json.loads(raw))
        window.activeWorkflowId = window.workflowStore.activeWorkflowId
        window.workflowController._renderActive()
        window._refreshWorkflowTabs()
        window.loadedProjectPath = str(args.project)
        window.runtimePanelState.lastMessage = str(args.project)
        window._refreshRuntimePanelView()
        window.updateToolbarState()
    else:
        ports = dict.fromkeys(["image", "frame", "blockId", "deviceTimestamp", "actualExposureUs"], "Any")
        window.flowScene.addFlowNode(FlowNodeViewModel("camera", "Huaray Camera - 192.168.125.28", 40, 40, {}, ports))
        window.flowScene.addFlowNode(FlowNodeViewModel("output", "Workflow Output", 500, 40, ports, {}))
        for name in ports:
            window.flowScene.renderEdge(FlowEdgeViewModel("camera", name, "output", name))
    capture("main", window, (args.width, args.height))
    report["initial_zoom"] = window.flowView.getZoomFactor()
    report["sidebar"] = {"collapsed": window.isSidebarCollapsed, "width": window.sidebarContainer.width(),
                         "minimum": window.sidebarContainer.minimumWidth(), "maximum": window.sidebarContainer.maximumWidth()}
    window.expandSidebar()
    window.flowScene.setNodeSelected("camera")
    capture("main-expanded", window, (args.width, args.height))
    entry = ProjectEntryDialog()
    entry.setRecentProjects([{"projectName": "Camera Capture", "projectPath": str(args.project)}])
    capture("project-entry", entry)
    param = NodeParamDialog()
    schema = {"type": "object", "properties": {
        "ipAddress": {"type": "string"}, "captureTimeoutMs": {"type": "integer", "minimum": 1},
        "triggerMode": {"type": "string", "enum": ["hardware", "software", "freeRun"]},
        "imagePath": {"type": "string", "xWidget": "file"},
    }}
    param.setNodeContext("camera", "vision.io.huaray_camera", schema, {
        "ipAddress": "192.168.125.28", "captureTimeoutMs": 5000,
        "imagePath": str(args.project.parent / "images/reference.png"),
    })
    capture("parameters", param)
    counters = GlobalCountersDialog(runtime)
    capture("global-counters", counters)
    log = LogDialog()
    log.setLogs(["[INFO] Designer ready", "[WARN] Camera preview is disconnected"])
    capture("logs", log)
    preview = WorkflowPackageImportPreview(
        sourceRootWorkflowId="capture", rootWorkflowId="capture", workflowIdMap={"capture": "capture"},
        workflows=(WorkflowPackageWorkflowPreview(sourceWorkflowId="capture", workflowId="capture", name="Camera Capture", isRoot=True),),
        conflicts=(), dependencies=(), requiredOperators=("vision.io.huaray_camera",), missingOperators=(), externalPaths=(),
    )
    package = WorkflowPackagePreviewDialog(preview)
    capture("workflow-package", package)
    manager = OperatorEditorManager(runtimeClient=runtime, settingsStore=settings,
                                    applyParams=lambda *_: True, appendLog=lambda *_: None, cacheRoot=output / "ui-cache")
    definitions = []
    for directory in ("huaray_camera", "roi", "histogram"):
        manifest = json.loads((ROOT / "src/emo_master/plugins/builtins" / directory / "manifest.json").read_text(encoding="utf-8"))
        definitions.append(manifest)
        editor = manager.open(projectId="visual-check", workflowId="capture", nodeId=directory,
                              operatorId=manifest["operatorId"], displayName=manifest["displayName"],
                              schema=manifest["paramSchema"], values={},
                              operatorDefinition={"version": manifest["version"], "editorSpec": manifest["editor"], "editorIssues": []})
        if getattr(editor, "_controller", None) is None:
            raise AssertionError(f"{directory}: {editor._statusLabel.text()}")
        capture(directory, editor)
    window.operatorBubble.setOperators([{"operatorId": m["operatorId"], "displayName": m["displayName"], "iconKey": "source"} for m in definitions])
    capture("operator-search", window.operatorBubble, (560, 340))
    image = QImage(640, 360, QImage.Format_RGB32)
    image.fill(QColor("#e5e7eb"))
    painter = QPainter(image)
    for index, color in enumerate(("#2563eb", "#16a34a", "#eab308", "#dc2626")):
        painter.fillRect(40 + index * 145, 60, 110, 240, QColor(color))
    painter.end()
    fixture = output / "preview-fixture.png"
    image.save(str(fixture))
    window.runtimePanelState.latestImagePath = str(fixture)
    window._refreshPreviewImage()
    capture("main-preview", window, (args.width, args.height))
    window.collapseSidebar()
    window.focusGraphContent()
    capture("main-fit", window, (args.width, args.height))
    if args.cross_screens:
        window.collapseSidebar()
        window.flowView.setZoomFactor(1.2)
        report["cross_screen"] = []
        for index, target in enumerate([*app.screens(), app.screens()[0]]):
            capture(f"screen-move-{index}", window, (1200, 700), target)
            report["cross_screen"].append({"screen": window.windowHandle().screen().name(),
                                           "dpr": window.devicePixelRatioF(),
                                           "zoom": window.flowView.getZoomFactor(),
                                           "sidebar": window.sidebarContainer.width()})
    report["node_geometry"] = [{"node": key, "size": [item.rect().width(), item.rect().height()],
                               "overflow": [child.text() for child in item.childItems()
                                            if hasattr(child, "text") and not item.rect().contains(child.mapRectToParent(child.boundingRect()))]}
                              for key, item in window.flowScene._nodeItems.items()]
    failures = []
    if args.project.exists():
        report["project_sha256_after"] = hashlib.sha256(args.project.read_bytes()).hexdigest()
        if report["project_sha256_after"] != report["project_sha256"]:
            failures.append("source project changed")
    for name, data in report["windows"].items():
        if any(value > available for value, available in zip(data["size"], data["available"])):
            failures.append(f"{name}: window exceeds available screen")
        if data["text_clipping"]:
            failures.append(f"{name}: clipped labels")
    if any(item["overflow"] for item in report["node_geometry"]):
        failures.append("node text exceeds node bounds")
    for move in report.get("cross_screen", []):
        if abs(move["zoom"] - 1.2) > 0.001 or move["sidebar"] != 36:
            failures.append("screen move changed zoom or collapsed width")
    report["failures"] = failures
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    manager.closeAll()
    for widget in (param, entry, counters, log, package, window):
        widget.close()
    app.processEvents()
    print(json.dumps(report, ensure_ascii=True))
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
