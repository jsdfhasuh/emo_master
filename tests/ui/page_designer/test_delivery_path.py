"""Designer UI -> immutable package -> relocated standalone native process."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import grpc
from PySide2.QtCore import Qt
from PySide2.QtTest import QTest
from PySide2.QtWidgets import QFileDialog, QMessageBox, QPushButton, QApplication

from emo_master.apps.designer.services.runtime_client import RuntimeClient
from emo_master.apps.designer.state.project_store import saveProject
from emo_master.apps.designer.ui.main_window import MainWindow
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2_grpc as rpc
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from emo_master.apps.runtime.release_host import requestStop
from emo_master.core.project.delivery_store import DeliveryStore
from emo_master.core.project.models import ProjectDocument
from examples.runtime_pages_p2 import sampleProject


def waitFor(predicate, seconds=30):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        QApplication.processEvents()
        if predicate():
            return
        time.sleep(.01)
    raise AssertionError('bounded delivery path deadline')


def testDesignerExportRelocatedStandalonePath(qtApp, tmp_path, monkeypatch):
    repo = Path(__file__).resolve().parents[3]
    out = Path(os.environ.get('P5_SCREEN_DIR', str(tmp_path/'evidence')))
    out.mkdir(parents=True, exist_ok=True)
    packageDir = out/'packages'
    monkeypatch.setenv('EMO_PAGE_DESIGNER', '1')
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **k: QMessageBox.Discard)
    monkeypatch.setattr(QMessageBox, 'information', lambda *a, **k: QMessageBox.Ok)
    errors = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: errors.append(args[-1]))
    draft = tmp_path/'designer-project'
    draft.mkdir()
    payload = sampleProject(draft).model_dump()
    payload['presentation'] = json.loads((repo/'docs/evidence/p4/c-accepted-visible/screens/two-pages.json').read_text(encoding='utf-8'))
    saveProject(draft, ProjectDocument.model_validate(payload).model_dump())
    runtime = RuntimeService(dbPath=tmp_path/'designer.sqlite3', workspaceRoot=tmp_path/'designer-jobs')
    window = MainWindow(RuntimeClient(runtime))
    process = None
    record = {}
    try:
        assert window.loadProjectDirectory(str(draft))
        window.resize(1480, 920)
        window.show()
        waitFor(lambda: window.operatorCatalogController.state == 'ready')
        window.pageCoordinator.showPages()
        editor = window.pageCoordinator.editor
        for index in range(2):
            editor.pageList.setCurrentRow(index)
            label = next(c for c in editor.store.snapshot().pages[editor.pageId].components if c.type == 'text')
            editor.tools.select(label.componentId)
            editor.tools.fields['text'].setText('测试项目交付 · 本地图像 · 真实 Blob / Count')
            QTest.mouseClick(next(w for w in editor.findChildren(QPushButton) if w.text() == '应用属性 / 布局'), Qt.LeftButton)
        editor.pageList.setCurrentRow(0)
        assert window.saveProjectToDirectory(str(draft))
        presentation = window.pageCoordinator.session.document().presentation.model_dump()
        assert window.loadProjectDirectory(str(draft))
        window.pageCoordinator.showPages()
        assert window.pageCoordinator.session.document().presentation.model_dump() == presentation
        assert window.grab().save(str(out/'01-designer-saved.png'))
        monkeypatch.setattr(QFileDialog, 'getExistingDirectory', lambda *a, **k: str(packageDir))
        next(a for a in window.mainToolbar.actions() if a.text() == '导出测试项目包').trigger()
        assert not errors, errors
        package = next(packageDir.glob('*.vxpkg'))
        packageHash = hashlib.sha256(package.read_bytes()).hexdigest()
        window.pageCoordinator.session.presentation.renamePage(presentation['pageOrder'][0], '后续草稿修改不进入旧包')
        assert window.saveProjectToDirectory(str(draft))
        assert hashlib.sha256(package.read_bytes()).hexdigest() == packageHash
        window.close()
        waitFor(lambda: not window.isVisible())
        runtime.close()
        # An empty directory with Chinese/spaces. The original path no longer exists.
        store = DeliveryStore(tmp_path/'独立 空目录')
        revision = store.importPackage(package)
        assert store.state()['active'] is None
        store.activate(revision)
        draft.rename(tmp_path/'原开发项目 已移走')
        assert not draft.exists()
        doc, manifest = store.verify(revision)
        assert doc.presentation.model_dump() == presentation
        assert str(draft) not in (store.revisionPath(revision)/'project.json').read_text(encoding='utf-8')
        (out/'two-pages.json').write_text(doc.presentation.model_dump_json(indent=2), encoding='utf-8')
        data = tmp_path/'独立 Runtime 数据'
        ready = data/'ready.json'
        env = dict(os.environ, PYTHONPATH=str(repo/'src'))
        with (out/'runtime.log').open('wb') as log:
            process = subprocess.Popen([sys.executable, str(repo/'scripts/p5_runtime.py'), '--store', str(store.root),
                '--data', str(data), '--ready-file', str(ready)], cwd=tmp_path, env=env,
                stdout=log, stderr=log)
            waitFor(lambda: ready.exists() or process.poll() is not None)
            assert process.poll() is None, (out/'runtime.log').read_text(encoding='utf-8', errors='replace')
            info = json.loads(ready.read_text())
            with grpc.insecure_channel(info['address']) as channel:
                stub = rpc.DisplayServiceStub(channel)
                assert not stub.ListJobs(pb.DisplayEmpty(), timeout=3).jobs
                command = [sys.executable, str(repo/'scripts/p5_view_probe.py'), '--ready-file', str(ready)]
                for index in [1, 2]:
                    destination = out/f'viewer-{index}'
                    jobArgs = [] if index == 1 else ['--job', record['job']]
                    result = subprocess.run([*command, '--output', str(destination), *jobArgs], cwd=tmp_path,
                        env=env, capture_output=True, timeout=60)
                    (out/f'viewer-{index}.log').write_bytes(result.stdout + result.stderr)
                    assert result.returncode == 0, (result.stdout + result.stderr).decode(errors='replace')
                    view = json.loads((destination/'view.json').read_text())
                    if index == 1:
                        record.update(job=view['job'], result_key=view['result_key'])
                    else:
                        assert view['job'] == record['job'] and view['result_key'] == record['result_key']
                    assert process.poll() is None
                    assert len(stub.ListJobs(pb.DisplayEmpty(), timeout=3).jobs) == 1
                record['viewer_exit_and_reconnect_preserved_runtime'] = True
                record['no_implicit_job_before_start'] = True
            stop = subprocess.run([sys.executable, str(repo/'scripts/p5_runtime.py'), '--stop', '--ready-file', str(ready)],
                cwd=tmp_path, env=env, capture_output=True, timeout=10)
            assert stop.returncode == 0, stop.stderr
            process.wait(timeout=20)
            assert process.returncode == 0 and not ready.exists()
        record.update(package=package.name, package_sha256=packageHash, revision=revision,
            designer_saved_reopened=True, exported_draft_is_frozen=True, original_resource_path_unavailable=True,
            imported_presentation_unchanged=True, non_project_cwd=str(tmp_path),
            imported_root=str(store.root), explicit_host_stop_confirmed=True, manifest=manifest)
        (out/'path.json').write_text(json.dumps(record, indent=2), encoding='utf-8')
    finally:
        window.close()
        runtime.close()
        if process and process.poll() is None:
            requestStop(ready)
            process.wait(timeout=20)
