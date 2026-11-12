"""Read-only Designer switch measurement; no Runtime service or device access."""
import argparse
import faulthandler
import hashlib
import importlib
import json
import logging
import os
from pathlib import Path
import subprocess
import tempfile
import time
from types import SimpleNamespace


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--detailed", action="store_true")
    parser.add_argument("--baseline", help="Load only the changed modules from this fixed git SHA")
    parser.add_argument("--platform", default="windows", choices=("windows", "offscreen"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[3]
    os.environ["QT_QPA_PLATFORM"] = args.platform
    data = Path(tempfile.mkdtemp(prefix="emo-page-switch-"))
    os.environ["EMO_RUNTIME_DATA_DIR"] = str(data / "runtime-data")
    for key in ("EMO_RUNTIME_DB_PATH", "EMO_RUNTIME_TARGET", "EMO_DESIGNER_SETTINGS_PATH"):
        os.environ.pop(key, None)
    import emo_master

    modules = ["apps.designer.state.workflow_store", "apps.designer.state.presentation_store",
               "ui.presentation.renderer", "apps.designer.page_designer.preview",
               "apps.designer.page_designer.tools", "apps.designer.page_designer.workspace",
               "apps.designer.page_designer.coordinator"]
    if args.baseline:
        for name in modules:
            module = importlib.import_module("emo_master." + name)
            relative = "src/emo_master/" + name.replace(".", "/") + ".py"
            source = subprocess.check_output(["git", "show", args.baseline + ":" + relative], cwd=root)
            exec(compile(source, module.__file__, "exec"), module.__dict__)

    from PySide2.QtCore import QSettings, Qt, QCoreApplication, QEvent
    from PySide2.QtWidgets import QApplication, QMessageBox
    from emo_master.apps.designer.ui import main_window
    from emo_master.apps.designer.main import applyDesignerStyle
    from emo_master.apps.designer.services.performance_probe import DesignerPerformanceProbe, sourceIdentity
    from emo_master.core.plugin.registry import PluginRegistry

    def settings(*_):
        value = QSettings(str(data / "designer.ini"), QSettings.IniFormat)
        value.setFallbacksEnabled(False)
        return value

    def forbidden(*_args, **_kwargs):
        raise AssertionError("measurement must not call Runtime load/start/write")

    main_window.QSettings = settings
    QMessageBox.question = lambda *_a, **_k: QMessageBox.Discard
    app = QApplication([])
    applyDesignerStyle(app)
    faulthandler.dump_traceback_later(120, exit=True)
    beforeHash = hashlib.sha256(args.project.read_bytes()).hexdigest()
    scan = PluginRegistry(emo_master.__version__).scan(root / "src/emo_master/plugins/builtins")
    assert not scan.rejectedOperators, scan.rejectedOperators
    operators = [item.manifest for item in scan.activeOperators.values()]
    window = main_window.MainWindow(SimpleNamespace(listOperators=lambda: operators,
        loadProject=forbidden, startJob=forbidden, writeProject=forbidden))
    window.setAttribute(Qt.WA_DontShowOnScreen)
    window.resize(1600, 900)
    window.operatorCatalog = window.operatorCatalogController.applyOperators(operators, window._classifyOperator)
    assert window.loadProjectDirectory(str(args.project), confirmed=True)
    window.show()
    for _ in range(4):
        app.processEvents()
    coordinator = window.pageCoordinator
    before, history = coordinator.session.payload(), len(coordinator.session._undo)
    snapshots = []
    toPayload = window.workflowStore.toPayload

    def countPayload(*args, **kwargs):
        snapshots.append(None)
        return toPayload(*args, **kwargs)

    window.workflowStore.toPayload = countPayload
    recorder = DesignerPerformanceProbe({}, detailed=args.detailed)
    recorder.start()
    start = time.perf_counter()
    coordinator.chrome.workspaceActions[1].trigger()
    opened = time.perf_counter()
    for _ in range(4):
        app.processEvents()
    ready = time.perf_counter()
    recorder.stop()
    snapshotCount = len(snapshots)
    window.workflowStore.toPayload = toPayload
    assert coordinator.pageActive()
    assert coordinator.session.payload() == before
    assert len(coordinator.session._undo) == history
    assert not coordinator.session.dirty
    editor = coordinator.editor
    assert editor.tools.choices
    args.output.mkdir(parents=True, exist_ok=True)
    assert window.grab().save(str(args.output / "page.png"))
    repeat = []
    for _ in range(3):
        coordinator.showFlow()
        startRepeat = time.perf_counter()
        coordinator.chrome.workspaceActions[1].trigger()
        app.processEvents()
        repeat.append((time.perf_counter() - startRepeat) * 1000)
    assert coordinator.session.payload() == before
    assert len(coordinator.session._undo) == history
    report = recorder.report()
    source = sourceIdentity()
    for module in modules:
        relative = module.replace(".", "/") + ".py"
        source["files"][relative] = hashlib.sha256((root / "src/emo_master" / relative).read_bytes()).hexdigest()
    result = {"source": source, "baselineModulesFrom": args.baseline,
        "baselineModuleNames": modules if args.baseline else [],
        "platform": app.platformName(), "scale": window.devicePixelRatioF(),
        "detailed": args.detailed, "projectSha256": beforeHash,
        "workflows": len(before["workflows"]),
        "nodes": sum(len(item["nodes"]) for item in before["workflows"].values()),
        "pages": len(before["presentation"]["pages"]), "choices": len(editor.tools.choices),
        "operators": len(window.operatorCatalog),
        "openMs": (opened-start)*1000, "readyMs": (ready-start)*1000, "repeatMs": repeat,
        "wholeProjectSnapshots": snapshotCount,
        "unchangedProjectAndHistory": True,
        "limitations": "isolated native/offscreen Qt with stub Runtime; not manual acceptance or hardware execution"}
    (args.output / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    (args.output / "profile.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items()
                      if key not in {"source", "baselineModuleNames"}}, ensure_ascii=True), flush=True)
    window.close()
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    logging.shutdown()
    assert hashlib.sha256(args.project.read_bytes()).hexdigest() == beforeHash
    faulthandler.cancel_dump_traceback_later()


if __name__ == "__main__":
    main()
