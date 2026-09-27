import json
import os
from pathlib import Path
import subprocess
import sys
import time

import grpc
import pytest

from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2_grpc as rpc
from emo_master.apps.runtime.release_host import ReleaseHost, requestStop
from emo_master.clients.runtime.display_session import DisplaySession
from emo_master.core.project.delivery_store import DeliveryStore
from emo_master.core.project.package_builder import buildPageTestPackage
from examples.runtime_pages_p2 import sampleProject


def installed(tmp_path):
    draft = tmp_path/'draft'
    draft.mkdir()
    doc = sampleProject(draft)
    package = buildPageTestPackage(doc, draft, tmp_path/'packages')
    store = DeliveryStore(tmp_path/'imported')
    revision = store.importPackage(package)
    store.activate(revision)
    draft.rename(tmp_path/'unavailable')
    return store, revision


def waitFor(predicate, seconds=25):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        value = predicate()
        if value:
            return value
        time.sleep(.01)
    raise AssertionError('bounded release condition deadline')


def testReleaseIsExplicitAndViewersBorrowRuntime(tmp_path):
    store, revision = installed(tmp_path)
    host = ReleaseHost(store.root, tmp_path/'data')
    sessions = []
    try:
        snapshot = host.prepared.snapshot
        assert snapshot.mode == 'release' and snapshot.releaseRevision == revision
        assert snapshot.draftRevision is None
        assert '/release/' in snapshot.runtimeDbPath.replace('\\', '/')
        assert '/debug/' not in snapshot.outputRoot.replace('\\', '/')
        assert Path(snapshot.runtimeDbPath).resolve().is_relative_to((tmp_path/'data').resolve())
        with grpc.insecure_channel(host.address) as channel:
            stub = rpc.DisplayServiceStub(channel)
            assert len(stub.ListJobs(pb.DisplayEmpty(), timeout=3).jobs) == 0
            with pytest.raises(grpc.RpcError):
                stub.Prepare(pb.DisplayPrepareRequest(project_json=host.document.model_dump_json(),
                    resource_root=str(host.projectRoot)), timeout=3)
            job = stub.Start(pb.DisplayStartRequest(prepared_id=snapshot.snapshotId), timeout=20).job_id
            with pytest.raises(grpc.RpcError):
                stub.Start(pb.DisplayStartRequest(prepared_id=snapshot.snapshotId), timeout=3)
            sessions = [DisplaySession(host.address, job), DisplaySession(host.address, job)]
            def ready(session):
                value = session.readSnapshot().scopes.get('root')
                return value if value and value.images else None
            views = [waitFor(lambda s=s: ready(s)) for s in sessions]
            assert {v.result.identity.mode for v in views} == {'release'}
            assert len({v.result.identity.resultKey for v in views}) == 1
            assert all(v.result.status == 'COMPLETE' and len(v.images) == 1 for v in views)
            assert all(next(s.valueJson for s in v.result.sources if s.sourceId == 'count') == '2' for v in views)
            sessions[0].close()
            assert not host.runtime._closed
            assert len(stub.ListJobs(pb.DisplayEmpty(), timeout=3).jobs) == 1
            assert not sessions[1].stop.is_set()
            with pytest.raises(RuntimeError, match='in use'):
                store.activate(revision)
    finally:
        for session in sessions:
            session.close()
        host.close()
    assert host.closed
    store.activate(revision)


def testDuplicateDirectoryAndPortDoNotStopExistingHost(tmp_path):
    store, _ = installed(tmp_path)
    host = ReleaseHost(store.root, tmp_path/'data')
    try:
        with pytest.raises(RuntimeError, match='in use'):
            ReleaseHost(store.root, tmp_path/'other-data')
        # A second independent store still may not reuse the listening port.
        second = tmp_path/'second'
        second.mkdir()
        other, _ = installed(second)
        with pytest.raises(RuntimeError, match='aio startup failed'):
            ReleaseHost(other.root, tmp_path/'other-data', port=host.server.port)
        with grpc.insecure_channel(host.address) as channel:
            assert rpc.DisplayServiceStub(channel).Capabilities(pb.DisplayEmpty(), timeout=3).runtime_instance_id
        assert not host.runtime._closed
    finally:
        host.close()


def testSeparateSourceHostReadinessAndExplicitStop(tmp_path):
    store, _ = installed(tmp_path)
    root = Path(__file__).resolve().parents[2]
    data = tmp_path/'独立 运行数据'
    ready = data/'ready.json'
    env = dict(os.environ, PYTHONPATH=str(root/'src'))
    with (tmp_path/'host.log').open('wb') as log:
        process = subprocess.Popen([sys.executable, str(root/'scripts/p5_runtime.py'), '--store', str(store.root),
            '--data', str(data), '--ready-file', str(ready)], cwd=tmp_path, env=env,
            stdout=log, stderr=log)
        try:
            waitFor(lambda: ready.exists() or process.poll() is not None)
            assert process.poll() is None, (tmp_path/'host.log').read_text(encoding='utf-8', errors='replace')
            info = json.loads(ready.read_text())
            with grpc.insecure_channel(info['address']) as channel:
                stub = rpc.DisplayServiceStub(channel)
                assert stub.Capabilities(pb.DisplayEmpty(), timeout=3).runtime_instance_id == info['runtimeInstanceId']
                assert not stub.ListJobs(pb.DisplayEmpty(), timeout=3).jobs
            requestStop(ready)
            process.wait(timeout=20)
            assert process.returncode == 0
            assert not ready.exists()
        finally:
            if process.poll() is None:
                requestStop(ready)
                process.wait(timeout=20)


def testStandaloneImportsExcludeDesignerAndRuntimeHasNoQt(tmp_path):
    root = Path(__file__).resolve().parents[2]
    env = dict(os.environ, PYTHONPATH=str(root/'src'))
    for module in ['emo_master.apps.operator_view.main', 'emo_master.apps.runtime.release_host']:
        command = f'import {module}; import sys; assert not any(n.startswith("emo_master.apps.designer") for n in sys.modules)'
        if module.endswith('release_host'):
            command += '; assert "PySide2.QtWidgets" not in sys.modules'
        result = subprocess.run([sys.executable, '-c', command], cwd=tmp_path, env=env, capture_output=True, timeout=30)
        assert result.returncode == 0, result.stderr


def testReleaseOutputSiteUsesOwnedChineseDirectory(tmp_path):
    from emo_master.core.project.models import ProjectDocument
    draft = tmp_path/'draft'
    draft.mkdir()
    raw = sampleProject(draft).model_dump()
    raw['workflows']['main']['nodes'].insert(-1, {'nodeId': 'save', 'operatorId': 'vision.io.image_saver',
        'params': {'outputPath': 'D:/never-use-developer-output.png'}})
    raw['workflows']['main']['edges'].append({'fromNode': 'blob', 'fromPort': 'overlay', 'toNode': 'save', 'toPort': 'image'})
    raw['resources']['siteBindings'] = [{'target': {'workflowId': 'main', 'nodeId': 'save',
        'parameterPath': ['outputPath']}, 'field': 'result', 'purpose': 'output_file'}]
    doc = ProjectDocument.model_validate(raw)
    package = buildPageTestPackage(doc, draft, tmp_path/'out')
    store = DeliveryStore(tmp_path/'store')
    store.activate(store.importPackage(package))
    data = tmp_path/'中文 输出数据'
    with pytest.raises(ValueError, match='required site'):
        ReleaseHost(store.root, data)
    host = ReleaseHost(store.root, data, siteValues={'result': '图像/result.png'})
    try:
        with grpc.insecure_channel(host.address) as channel:
            job = rpc.DisplayServiceStub(channel).Start(pb.DisplayStartRequest(
                prepared_id=host.prepared.snapshot.snapshotId), timeout=20).job_id
        waitFor(lambda: host.runtime.jobRepository.get(job).isTerminal)
        assert host.runtime.jobRepository.get(job).status == 'COMPLETED'
        output = Path(host.prepared.snapshot.outputRoot)/'图像/result.png'
        assert output.read_bytes().startswith(b'\x89PNG')
        assert output.resolve().is_relative_to(data.resolve())
        assert not list(store.root.rglob('result.png'))
    finally:
        host.close()
