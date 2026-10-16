"""Open a real Designer and run one isolated local-image Job, then capture it."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(1, str(ROOT))
import emo_master  # noqa: E402,F401 - Initialize Windows DLL order before Qt.
from PySide2.QtCore import QCoreApplication, QEvent, Qt  # noqa: E402
from PySide2.QtTest import QTest  # noqa: E402
from PySide2.QtWidgets import QApplication, QMessageBox  # noqa: E402
from emo_master.apps.designer.main import configureHighDpi, applyDesignerStyle  # noqa: E402
from emo_master.apps.designer.services.runtime_client import RuntimeClient  # noqa: E402
from emo_master.apps.designer.state.project_store import saveProject  # noqa: E402
from emo_master.apps.designer.ui.main_window import MainWindow  # noqa: E402
from emo_master.apps.runtime.grpc_server.service import RuntimeService  # noqa: E402
from examples.flow_run_inspection import sampleProject  # noqa: E402


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT, encoding='utf-8').strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--width', type=int, default=1600)
    parser.add_argument('--height', type=int, default=900)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    document = sampleProject(output / 'project')
    saveProject(output / 'project', document.model_dump())
    configureHighDpi()
    app = QApplication([])
    applyDesignerStyle(app)
    runtime = RuntimeService(dbPath=output / 'runtime.sqlite3', workspaceRoot=output / 'jobs', logDirectory=output / 'logs')
    client = RuntimeClient(runtime)
    prefs = {}
    settings = SimpleNamespace(value=lambda key, default=None: prefs.get(key, default),
        setValue=lambda key, value: prefs.update({key: value}))
    window = MainWindow(client, settingsStore=settings)
    QMessageBox.question = lambda *_args, **_kwargs: QMessageBox.Discard
    result = {'head': git('rev-parse', 'HEAD'), 'dirty': git('status', '--porcelain=v1').splitlines(),
              'python': sys.version, 'qtPlatform': os.getenv('QT_QPA_PLATFORM'),
              'qtScale': os.getenv('QT_SCALE_FACTOR'), 'load': '240x360 fixture, two white objects, single real spawn Job',
              'runtimeDirectory': str(output), 'screenshots': [],
              'captureSourceSha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'fixtureSourceSha256': hashlib.sha256((ROOT / 'examples/flow_run_inspection.py').read_bytes()).hexdigest()}

    def wait(predicate, seconds=30):
        deadline = time.monotonic() + seconds
        while not predicate():
            if time.monotonic() >= deadline:
                raise AssertionError('GUI condition not reached within outer wait')
            app.processEvents()
            time.sleep(.005)
        app.processEvents()

    def capture(name):
        # Event-driven readiness was already checked; pump pending paint events.
        for _ in range(8):
            app.processEvents()
        assert window.runResultTools.geometry().top() > window.previewImageLabel.geometry().bottom()
        path = output / (name + '.png')
        assert window.grab().save(str(path))
        result['screenshots'].append({'file': path.name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'clientSize': [window.width(), window.height()], 'dpr': window.devicePixelRatioF(),
            'inspectionAreaHeight': window.nodeDetailsScroll.height(),
            'previewImageRect': window.previewImageLabel.geometry().getRect(),
            'toolsRect': window.runResultTools.geometry().getRect(),
            'originRect': window.runResultTools.origin.geometry().getRect(),
            'toolsMinimum': [window.runResultTools.minimumSizeHint().width(), window.runResultTools.minimumSizeHint().height()],
            'toolsHfw': window.runResultTools.heightForWidth(window.runResultTools.width()),
            'toolsLayoutHfw': window.runResultTools.layout().heightForWidth(window.runResultTools.width())})

    def select(node):
        window.focusGraphContent()
        center = window.flowScene.getNodeCenter(node)
        from PySide2.QtCore import QPointF
        point = window.flowView.mapFromScene(QPointF(*center))
        QTest.mouseClick(window.flowView.viewport(), Qt.LeftButton, pos=point)
        wait(lambda: window.flowModel.selectedNodeId == node, 5)
        window.nodeDetailsScroll.verticalScrollBar().setValue(0)

    try:
        window.resize(args.width, args.height)
        window.show()
        wait(lambda: window.operatorCatalogController.state == 'ready')
        assert window.loadProjectDirectory(str(output / 'project'))
        wait(lambda: 'count' in window.flowModel.nodes)
        result['jobsBeforeStart'] = len(runtime.jobRepository.all())
        assert result['jobsBeforeStart'] == 0
        window.autoLayoutNodes()
        select('count')
        capture('before-run')
        # The original Designer controller explicitly starts the only Job.
        window.startJob()
        wait(lambda: window.currentJobId is not None)
        job = window.currentJobId
        wait(lambda: not window.runtimeController._jobActive)
        wait(lambda: window.runtimeController._worker is None)
        assert runtime.jobRepository.get(job).status == 'COMPLETED'
        select('count')
        assert 'count = 2' in window.nodeRunDetailsCard.text()
        assert 'blobs = 集合 · 2 项' in window.nodeRunDetailsCard.text()
        capture('count-result')
        result['countText'] = window.nodeRunDetailsCard.text()
        select('presence')
        assert 'left = 2' in window.nodeRunDetailsCard.text()
        assert 'result = true' in window.nodeRunDetailsCard.text()
        assert window.runResultTools.openFile.isEnabled()
        assert window.runtimePanelState.latestImagePath
        assert Path(window.runtimePanelState.latestImagePath).is_file()
        capture('compare-result')
        result['compareText'] = window.nodeRunDetailsCard.text()
        result['artifact'] = window.runtimePanelState.latestArtifact
        result['job'] = asdict(runtime.jobRepository.get(job))
        result['retainedInspectionBytes'] = window.runtimePanelState.nodeInspection.retainedBytes
        result['jobsAfterSelectingNodes'] = len(runtime.jobRepository.all())
        assert result['jobsAfterSelectingNodes'] == 1
        window.runResultTools.logs.click()
        wait(lambda: window.logDock.isVisible(), 5)
        capture('run-logs')
        result['events'] = [asdict(event) for event in runtime.eventStore.read(job)]
        window.logDock.hide()
        window.updateNodeParams('load', {'imagePath': str(output / 'project' / 'missing.png')})
        window.startJob()
        wait(lambda: window.currentJobId is not None and window.currentJobId != job)
        failedJob = window.currentJobId
        wait(lambda: not window.runtimeController._jobActive)
        wait(lambda: window.runtimeController._worker is None)
        assert runtime.jobRepository.get(failedJob).status == 'FAILED'
        select('load')
        assert '执行失败' in window.nodeRunDetailsCard.text()
        assert 'E_INPUT_MISSING' in window.nodeRunDetailsCard.text()
        assert '无结果' in window.nodeRunDetailsCard.text()
        assert not window.runResultTools.openFile.isEnabled()
        assert window.runtimePanelState.latestImagePath is None
        capture('failed-run')
        result['failedJob'] = asdict(runtime.jobRepository.get(failedJob))
        result['failedText'] = window.nodeRunDetailsCard.text()
        select('count')
        assert '本次尚未收到' in window.nodeRunDetailsCard.text()
        assert 'count = 2' not in window.nodeRunDetailsCard.text()
        result['countAfterFailure'] = window.nodeRunDetailsCard.text()
        assert len(runtime.jobRepository.all()) == 2  # Two explicit starts, no observers starting work.
        result['status'] = 'PASS'
    except BaseException as error:
        result['status'] = 'FAIL'
        result['failure'] = str(error)[:800]
        raise
    finally:
        window.close()
        window.shutdownOperatorDisplay()
        client.close()
        runtime.close()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        result['windowClosed'] = not window.isVisible()
        result['runtimeClosed'] = runtime._closed
        result['inspectionAfterClose'] = window.runtimePanelState.nodeInspection.retainedBytes
        assert result['inspectionAfterClose'] == 0
        (output / 'measurements.json').write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    print(json.dumps({key: result[key] for key in ('status', 'retainedInspectionBytes', 'jobsBeforeStart', 'jobsAfterSelectingNodes', 'windowClosed', 'runtimeClosed')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
