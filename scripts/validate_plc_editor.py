"""Capture native PLC editors using a loopback simulator; never start a Job."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))


def sourceHashes():
    paths = [
        "proto/runtime.proto",
        "src/emo_master/apps/designer/operator_editors/controller_protocol.py",
        "src/emo_master/apps/designer/operator_editors/trust.py",
        "src/emo_master/apps/designer/services/runtime_client.py",
        "src/emo_master/apps/runtime/grpc_server/aio_entry.py",
        "src/emo_master/apps/runtime/grpc_server/generated/runtime_pb2.py",
        "src/emo_master/apps/runtime/grpc_server/generated/runtime_pb2_grpc.py",
        "src/emo_master/apps/runtime/grpc_server/service.py",
        "src/emo_master/apps/runtime/presentation/service.py",
        "src/emo_master/apps/runtime/preview/plc_debug.py",
        "src/emo_master/plugins/builtins/_slmp.py",
        "src/emo_master/plugins/builtins/_plc_editor.py",
        "src/emo_master/plugins/builtins/plc_slmp_read/manifest.json",
        "src/emo_master/plugins/builtins/plc_slmp_read/ui/editor.ui",
        "src/emo_master/plugins/builtins/plc_slmp_write/manifest.json",
        "src/emo_master/plugins/builtins/plc_slmp_write/ui/editor.ui",
        "tests/plugins/test_slmp_debug_transport.py",
        "tests/runtime/test_plc_debug.py",
        "tests/runtime/plc_debug_server.py",
        "tests/designer/test_plc_editor.py",
        "scripts/validate_plc_editor.py",
    ]
    tracked = subprocess.check_output(
        ["git", "ls-files", "-z", "--", "src", "proto", "tests"], cwd=ROOT, text=True,
    ).split("\0")
    return {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
            for path in sorted(set(paths) | {path for path in tracked if path})}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--platform", choices=["windows", "offscreen"], default="windows")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    os.environ["QT_QPA_PLATFORM"] = args.platform

    import emo_master  # noqa: F401 - initialize Qt DLL paths
    from PySide2.QtCore import QCoreApplication, QEvent, QEventLoop, QSettings, QTimer, Qt
    from PySide2.QtGui import QFontDatabase, QGuiApplication, QImage, QPainter
    from PySide2.QtWidgets import QApplication, QLineEdit, QLabel, QCheckBox, QSpinBox
    from emo_master.apps.designer.main import _configureHighDpi, applyDesignerStyle
    from emo_master.apps.designer.operator_editors.manager import OperatorEditorManager
    from emo_master.apps.designer.services.runtime_client import RuntimeClient
    from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
    from emo_master.apps.runtime.grpc_server.service import RuntimeService
    from tests.runtime.plc_debug_server import PlcDebugServer
    from tests.runtime.test_builtin_communication_operator_workflows import _project

    _configureHighDpi(QCoreApplication, Qt, QGuiApplication)
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(output / "settings"))
    app = QApplication([])
    # Windows offscreen Qt does not enumerate installed system fonts.
    if sys.platform == "win32" and not QFontDatabase().families():
        fontPath = Path(os.getenv("SystemRoot", "C:/Windows")) / "Fonts/msyh.ttc"
        assert QFontDatabase.addApplicationFont(str(fontPath)) >= 0, fontPath
    assert QFontDatabase().families(), "Qt has no available fonts"
    applyDesignerStyle(app)
    fontProbe = QImage(240, 48, QImage.Format_ARGB32)
    fontProbe.fill(Qt.white)
    painter = QPainter(fontProbe)
    painter.setFont(app.font())
    painter.setPen(Qt.black)
    painter.drawText(fontProbe.rect(), Qt.AlignCenter, "PLC 123")
    painter.end()
    textPixels = sum(fontProbe.pixelColor(x, y).value() < 128
                     for x in range(fontProbe.width()) for y in range(fontProbe.height()))
    assert textPixels > 30, "Qt failed to render text"
    report = {"status": "FAIL", "platform": args.platform, "scale": os.getenv("QT_SCALE_FACTOR", "1"),
              "fontDpi": os.getenv("QT_FONT_DPI", "platform-default"),
              "fontFamily": app.font().family(), "textProbePixels": textPixels,
              "hardwareAcceptance": "NOT_RUN", "jobExecution": "NOT_RUN", "cases": []}
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    report["head"] = head
    report["sourceHashes"] = sourceHashes()

    def waitFor(predicate, timeout=4):
        deadline = time.monotonic() + timeout
        while not predicate():
            app.processEvents()
            if time.monotonic() >= deadline:
                raise AssertionError("Qt PLC operation did not complete")
            time.sleep(0.005)
        app.processEvents()

    class Settings:
        def value(self, _key, default=None):
            return default

        def setValue(self, _key, _value):
            pass

    with tempfile.TemporaryDirectory(prefix="plc-editor-validation-") as temporary, PlcDebugServer() as plc:
        temporary = Path(temporary)
        projectDir = temporary / "project"
        projectDir.mkdir()
        document = _project(plc.port)
        (projectDir / "project.json").write_text(document.model_dump_json(), encoding="utf-8")
        runtime = RuntimeService(dbPath=temporary / "runtime.db", workspaceRoot=temporary / "jobs")
        client = RuntimeClient(runtime)
        manager = OperatorEditorManager(runtimeClient=client, settingsStore=Settings(),
            applyParams=lambda _key, _params: True, appendLog=lambda *_args: None,
            cacheRoot=temporary / "editor-cache")
        try:
            assert runtime.LoadProject(pb.LoadProjectRequest(project_path=str(projectDir)), None).ok
            definitions = {definition.operatorId: definition for definition in client.listOperators()}
            for suffix, nodeId in (("read", "read"), ("write", "write")):
                operatorId = "communication.plc.slmp_" + suffix
                definition = definitions[operatorId]
                window = manager.open(projectId=document.project.projectId, workflowId="main", nodeId=nodeId,
                    operatorId=operatorId, displayName=definition.displayName,
                    schema=definition.paramSchema, values=plc.params(startAddress=798, count=3, values=[11, 22, 33]),
                    operatorDefinition={"version": definition.version, "editorSpec": definition.editorSpec,
                                        "editorIssues": list(definition.editorIssues)})
                controller = window._controller
                assert controller is not None
                beforeRequests = len(plc.requests)
                app.processEvents()
                assert controller.tabs.currentIndex() == 0 and len(plc.requests) == beforeRequests
                assert controller.tabs.tabText(0) == "参数" and controller.tabs.tabText(1) == "运行"
                controller.tabs.setCurrentIndex(1)
                controller.connect()
                waitFor(lambda: bool(controller._sessionId) and controller._operation is None)
                assert not controller._writeEnabled
                controller.countSpin.setValue(3)
                plc.words.update({798: 11, 799: 22, 800: 33})
                controller.read()
                waitFor(lambda: controller._operation is None and controller.resultTable.rowCount() == 3)
                controller.writeEnabledCheck.setChecked(True)
                waitFor(lambda: controller._operation is None and controller._writeEnabled)
                for row, value in enumerate((101, 202, 303)):
                    control = controller.writeTable.cellWidget(row, 1)
                    if isinstance(control, QSpinBox):
                        control.setValue(value)
                    else:
                        assert isinstance(control, QLineEdit)
                        control.setText(str(value))
                controller.write()
                waitFor(lambda: controller._operation is None and plc.words.get(798) == 101)
                assert [plc.words[address] for address in (798, 799, 800)] == [101, 202, 303]
                formal = controller.collectParams()

                def capture(view, physicalWidth, physicalHeight):
                    screen = window.screen().availableGeometry()
                    dpr = window.devicePixelRatioF()
                    chrome = window.frameGeometry().size() - window.size()
                    requested = [int(physicalWidth / dpr), int(physicalHeight / dpr)]
                    size = requested[:]
                    if args.platform == "windows":
                        size = [min(size[0], screen.width() - chrome.width() - 16),
                                min(size[1], screen.height() - chrome.height() - 16)]
                    window.move(screen.x() + 8, screen.y() + 8)
                    # Qt recalculates height-for-width constraints after native
                    # resize events; settle those before testing the final size.
                    for _ in range(3):
                        window.resize(*size)
                        app.processEvents()
                        app.processEvents()
                    window.move(screen.x() + 8, screen.y() + 8)
                    loop = QEventLoop()
                    QTimer.singleShot(50, loop.quit)
                    loop.exec_()
                    frameFits = screen.contains(window.frameGeometry())
                    report["lastGeometry"] = {"requested": size, "actual": [window.width(), window.height()],
                        "minimumHint": [window.minimumSizeHint().width(), window.minimumSizeHint().height()],
                        "minimum": [window.minimumWidth(), window.minimumHeight()],
                        "screen": list(screen.getRect()), "frame": list(window.frameGeometry().getRect())}
                    assert window.size().width() == size[0], (view, window.size(), size)
                    assert window.size().height() == size[1], (view, window.size(), size)
                    assert args.platform != "windows" or frameFits
                    assert controller.collectParams() == formal
                    # Check actionable widgets, allowing deliberate table/scroll content clipping.
                    controls = [controller.tabs, window._applyButton, window._closeButton]
                    if controller.tabs.currentIndex() == 1:
                        controls += [controller.connectButton, controller.readButton, controller.writeButton,
                                     controller.pollCheck, controller.writeEnabledCheck, controller.resultTable,
                                     controller.writeTable, controller.hostEdit, controller.dataTypeCombo]
                    for control in controls:
                        if not control.isVisible():
                            continue
                        top = control.mapTo(window, control.rect().topLeft())
                        bottom = control.mapTo(window, control.rect().bottomRight())
                        assert window.rect().contains(top) and window.rect().contains(bottom), control.objectName()
                        if controller.controlsScroll.isAncestorOf(control):
                            viewport = controller.controlsScroll.viewport()
                            assert viewport.rect().contains(control.mapTo(viewport, control.rect().topLeft())), control.objectName()
                            assert viewport.rect().contains(control.mapTo(viewport, control.rect().bottomRight())), control.objectName()
                    for label in window.findChildren(QLabel):
                        if label.isVisible() and not label.wordWrap() and label.text() and label.pixmap() is None:
                            required = label.fontMetrics().horizontalAdvance(label.text())
                            assert required <= label.contentsRect().width() + 4, (label.text(), required, label.width())
                    filename = f"{suffix}-{view}-{physicalWidth}x{physicalHeight}.png"
                    pixmap = window.grab()
                    physicalSize = [pixmap.width(), pixmap.height()]
                    assert pixmap.save(str(output / filename))
                    if args.platform == "offscreen":
                        assert all(abs(actual - requested) <= 1 for actual, requested in
                                   zip(physicalSize, [physicalWidth, physicalHeight])), physicalSize
                    report["cases"].append({"operator": suffix, "view": view, "viewport": [physicalWidth, physicalHeight],
                        "requestedLogical": requested, "actualLogical": size, "dpr": dpr,
                        "actualPhysical": physicalSize,
                        "constrainedByScreen": requested != size, "frameFitsScreen": frameFits,
                        "status": "PASS", "screenshot": filename})

                for width, height in ((1280, 720), (1920, 1080)):
                    capture("D-runtime", width, height)
                controller.deviceCombo.setCurrentText("M")
                controller.dataTypeCombo.setCurrentText("bit")
                controller.addressSpin.setValue(100)
                controller.countSpin.setValue(1)
                plc.bits.update({99: True, 100: False, 101: True})
                valueControl = controller.writeTable.cellWidget(0, 1)
                assert isinstance(valueControl, QCheckBox)
                valueControl.setChecked(True)
                controller.write()
                waitFor(lambda: controller._operation is None and plc.bits.get(100) is True)
                assert plc.bits[99] and plc.bits[101]
                for width, height in ((1280, 720), (1920, 1080)):
                    capture("M-runtime", width, height)
                controller.advancedButton.setChecked(True)
                capture("advanced", 1280, 720)
                controller.advancedButton.setChecked(False)
                controller.tabs.setCurrentIndex(0)
                waitFor(lambda: not controller._writeEnabled and not controller._safetyPending())
                for width, height in ((1280, 720), (1920, 1080)):
                    capture("parameters", width, height)
                controller.disconnect()
                waitFor(lambda: not controller._sessionId and not controller._safetyPending())
                window.forceClose()
                QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            assert not runtime.jobRepository.all()
            assert not runtime.plcDebugManager._sessions
            report["loopback"] = {"connections": plc.connections, "requests": len(plc.requests),
                                  "MNeighborsUnchanged": True}
            assert sourceHashes() == report["sourceHashes"], "source changed during verification"
            report["status"] = "PASS"
        finally:
            if sys.exc_info()[1] is not None:
                report["failure"] = repr(sys.exc_info()[1])
            manager.closeAll()
            client.close()
            runtime.close()
            (output / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
