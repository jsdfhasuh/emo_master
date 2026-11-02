"""Exercise the actual QProcess handoff; the renderer executable belongs to Dot."""
import json
import sys
import time
from types import SimpleNamespace

import pytest

from emo_master.apps.designer.page_designer.web_preview import WebPreviewProcess, runtimePreviewContext
from emo_panel_protocol.preview import PreviewContext


def fixtureContext():
    return PreviewContext(mode='fixtures', projectId='project', packageRevision='a' * 64,
                          historyDefinitionRevision='b' * 64)


def spin(app, condition, seconds=5):
    deadline = time.monotonic() + seconds
    while not condition() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.005)
    assert condition()


def testPreviewLaunchUsesLoopbackContractAndCleansUp(retainedQtApplication, tmp_path):
    script = tmp_path / 'dot_preview_stub.py'
    script.write_text('import json,sys,time\n'
        'assert sys.argv[1] == "preview"\n'
        'assert sys.argv[sys.argv.index("--host")+1] == "127.0.0.1"\n'
        'assert "--no-control" in sys.argv and "--json" in sys.argv\n'
        'from pathlib import Path\n'
        'context=json.loads(Path(sys.argv[sys.argv.index("--context")+1]).read_text())\n'
        'assert context["mode"] == "fixtures" and context["readOnly"] is True\n'
        'assert context["projectId"] == "project" and "runtime" not in context\n'
        'print(json.dumps({"event":"ready","protocolVersion":"1",'
        '"url":"http://127.0.0.1:51234/?token=temporary"}),flush=True)\n'
        'time.sleep(30)\n', encoding='utf-8')
    owner = WebPreviewProcess(command=[sys.executable, str(script)])
    ready, failed = [], []
    owner.ready.connect(ready.append)
    owner.failed.connect(failed.append)
    try:
        owner.start(tmp_path / 'preview.emoweb', fixtureContext())
        directory = owner.directory.name
        spin(retainedQtApplication, lambda: bool(ready) or bool(failed))
        assert ready == ['http://127.0.0.1:51234/?token=temporary'] and not failed
    finally:
        owner.close()
        spin(retainedQtApplication, lambda: owner.finished)
        owner.deleteLater()
        retainedQtApplication.processEvents()
    from pathlib import Path
    assert not Path(directory).exists()


def testPreviewRejectsNonLoopbackStartup(retainedQtApplication, tmp_path):
    script = tmp_path / 'bad_renderer.py'
    value = json.dumps(dict(event='ready', protocolVersion='1', url='http://192.0.2.1:5000/?token=test'))
    script.write_text('import time\nprint(' + repr(value) + ',flush=True)\ntime.sleep(30)\n', encoding='utf-8')
    owner = WebPreviewProcess(command=[sys.executable, str(script)])
    ready, failed = [], []
    owner.ready.connect(ready.append)
    owner.failed.connect(failed.append)
    try:
        owner.start(tmp_path / 'preview.emoweb', fixtureContext())
        spin(retainedQtApplication, lambda: bool(failed))
        assert not ready and 'Panel Contract' in failed[0]
    finally:
        owner.close()
        spin(retainedQtApplication, lambda: owner.finished)
        owner.deleteLater()
        retainedQtApplication.processEvents()


def testRuntimePreviewHandsOffSelectedEndpointAndIdentityWithoutControllingJobs(retainedQtApplication, tmp_path):
    from pathlib import Path
    from dataclasses import replace
    from emo_master.apps.designer.services.runtime_connection import connectCandidate
    from emo_master.apps.operator_runtime.controller import ProductionRuntime
    from emo_master.core.presentation.history import historyRevision
    from tests.runtime.current_production_fixture import currentProductionProject
    from emo_panel_protocol.generated import runtime_pb2 as pb

    path, document = currentProductionProject(tmp_path / 'project')
    runtime = ProductionRuntime(tmp_path / 'runtime')
    client = owner = None
    try:
        runtime.load(path)
        profile = replace(runtime.connectionProfile, timeoutSeconds=1.5, maxReceiveMessageBytes=12 * 1024 * 1024)
        client, _info, _snapshot, _types = connectCandidate(profile)
        before = client.getRuntimeInfo()
        manifest = SimpleNamespace(projectId=document.project.projectId, packageRevision='a' * 64,
                                   historyDefinitionRevision=historyRevision(document.presentation))
        context = runtimePreviewContext(client, manifest, client.runtimeScope, client.runtimeService)
        assert context.runtime.connectionProfile() == profile
        assert context.runtime.runtimeInstanceId == runtime.runtime.runtimeInstanceId
        assert context.runtime.documentRevision == str(before.document_revision)
        assert not runtime.runtime.jobRepository.all()
        assert client.getRuntimeInfo() == before
        for field, value, message in [('runtime_instance_id', 'another-instance', '实例'),
                                     ('project_id', 'another-project', '正式工程'),
                                     ('history_definition_revision', 'c' * 64, '历史数据声明')]:
            changed = pb.RuntimeInfoReply()
            changed.CopyFrom(before)
            setattr(changed, field, value)
            with pytest.raises(ValueError, match=message):
                context.validateRuntimeInfo(changed)
        mismatch = SimpleNamespace(**vars(manifest))
        mismatch.historyDefinitionRevision = 'c' * 64
        with pytest.raises(ValueError, match='历史数据声明'):
            runtimePreviewContext(client, mismatch, client.runtimeScope, client.runtimeService)

        observed = tmp_path / 'received-context.json'
        script = tmp_path / 'online_renderer_stub.py'
        script.write_text('import json,sys,time\nfrom pathlib import Path\n'
            'context=Path(sys.argv[sys.argv.index("--context")+1]).read_text()\n'
            'Path(sys.argv[1]).write_text(context)\n'
            'print(json.dumps({"event":"ready","protocolVersion":"1",'
            '"url":"http://127.0.0.1:51234/?token=temporary"}),flush=True)\ntime.sleep(30)\n', encoding='utf-8')
        owner = WebPreviewProcess(command=[sys.executable, str(script), str(observed)])
        ready, failures = [], []
        owner.ready.connect(ready.append)
        owner.failed.connect(failures.append)
        owner.prepareRuntime(tmp_path / 'preview.emoweb', manifest, client)
        spin(retainedQtApplication, lambda: ready or failures)
        assert ready and not failures
        assert PreviewContext.model_validate_json(observed.read_text()) == context
        directory = Path(owner.directory.name)
        owner.close()
        spin(retainedQtApplication, lambda: owner.finished)
        assert not directory.exists()
        assert client.getRuntimeInfo() == before and not runtime.runtime.jobRepository.all()
    finally:
        if owner:
            owner.close()
            spin(retainedQtApplication, lambda: owner.finished)
            owner.deleteLater()
            retainedQtApplication.processEvents()
        if client:
            client.close()
        runtime.close()


def testPreviewPreparationRejectsSwitchedConnection():
    client = SimpleNamespace(runtimeScope='old', runtimeService=object())
    original = client.runtimeService

    def switched():
        client.runtimeScope = 'new'
        return None

    client.ensureReadOnlyEndpoint = switched
    with pytest.raises(ValueError, match='连接已切换'):
        runtimePreviewContext(client, None, 'old', original)


def testClosingDuringPreviewPreparationCannotLaunchProcess(retainedQtApplication, tmp_path, monkeypatch):
    import threading
    from emo_master.apps.designer.page_designer import web_preview

    entered, release = threading.Event(), threading.Event()

    def prepare(*_):
        entered.set()
        assert release.wait(3)
        return fixtureContext()

    monkeypatch.setattr(web_preview, 'runtimePreviewContext', prepare)
    client = SimpleNamespace(runtimeScope='scope', runtimeService=object())
    owner = WebPreviewProcess(command=[sys.executable])
    started = []
    owner.process.started.connect(lambda: started.append(True))
    try:
        owner.prepareRuntime(tmp_path / 'preview.emoweb', None, client)
        spin(retainedQtApplication, entered.is_set)
        pending = owner.pending
        owner.close()
        release.set()
        spin(retainedQtApplication, pending.done)
        assert owner.finished and not started
    finally:
        release.set()
        owner.close()
        owner.deleteLater()
        retainedQtApplication.processEvents()
