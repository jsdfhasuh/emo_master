"""Conversion errors belong to the writer policy, with balanced source events."""
import json
import multiprocessing
from pathlib import Path
import sqlite3
import tempfile
import traceback

import grpc
import pytest

from emo_master.apps.runtime.workflow.cancellation import CancellationToken
from emo_master.apps.runtime.workflow.context import RunContext
from emo_master.apps.runtime.workflow.runner import WorkflowRunner
from emo_master.core.contracts.sqlite_writer import OPERATOR_ID, SqliteWriterError, toStorage
from emo_master.core.workflow.compiler import WorkflowCompiler
from emo_master.plugins.builtins.sqlite_writer.operator import SqliteWriterOperator
from tests.sqlite_writer.test_backend import makeDb
from tests.sqlite_writer.test_dependencies import config, project


@pytest.mark.parametrize('value', [{'bad': '\ud800'}, {'\udfff': 1}, 10**5000], ids=['surrogate-value', 'surrogate-key', 'integer-encoding-limit'])
def testJsonEncodingFailuresAreTypedAndDoNotTruncate(value):
    with pytest.raises(SqliteWriterError) as failed:
        toStorage(value, 'JSON')
    assert failed.value.code == 'E_SQLITE_VALUE'
    assert json.loads(toStorage({'正常': [0, False, None, '', '😀']}, 'JSON')) == {'正常': [0, False, None, '', '😀']}


def encodingProbe(root, policy, sender):
    from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb, runtime_pb2_grpc as rpc
    from emo_master.apps.runtime.grpc_server.service import RuntimeService
    from emo_master.apps.runtime.main import createRuntimeServer
    from tests.runtime.runtime_test_utils import waitForTerminal
    root = Path(root)
    business = makeDb(root / 'business.sqlite3')
    payload = project()
    payload['workflows']['main']['nodes'][0]['params'] = config(business, failure=policy)
    (root / 'project.json').write_text(json.dumps(payload), encoding='utf-8')
    runtime = RuntimeService(dbPath=root / 'runtime.db', workspaceRoot=root / 'jobs')
    server = channel = None
    try:
        server, port, _ = createRuntimeServer(port=0, runtimeService=runtime)
        server.start()
        channel = grpc.insecure_channel(f'127.0.0.1:{port}')
        grpc.channel_ready_future(channel).result(timeout=5)
        stub = rpc.RuntimeServiceStub(channel)
        assert stub.LoadProject(pb.LoadProjectRequest(project_path=str(root)), timeout=3).ok
        started = stub.StartJob(pb.StartJobRequest(project_id='sqlite-test', inputs_json=json.dumps({'value': {'text': '\ud800'}})), timeout=3)
        assert started.ok, started.message
        terminal = waitForTerminal(runtime, started.job_id, timeoutSeconds=12)
        events = runtime.eventStore.readMerged(started.job_id)
        receipt = next(json.loads(e.payloadJson)['receipt'] for e in events if e.eventType == 'sqlite.write.finished')
        assert receipt['status'] == 'FAILED' and receipt['error']['code'] == 'E_SQLITE_VALUE'
        assert receipt['error']['mappingRow'] == 0 and receipt['rowsAffected'] == 0
        assert terminal.status == ('COMPLETED' if policy == 'continue' else 'FAILED')
        assert [e.eventType for e in events if e.nodeId == 'input'] == ['node.started', 'node.completed']
        assert any(e.eventType == 'node.log' and e.level == 'ERROR' for e in events)
        with sqlite3.connect(business) as connection:
            assert connection.execute('SELECT count(*) FROM records').fetchone()[0] == 0
        sender.send({'policy': policy, 'status': terminal.status, 'receipt': receipt})
    except BaseException:
        sender.send({'error': traceback.format_exc()})
        raise
    finally:
        if channel is not None:
            channel.close()
        if server is not None:
            server.stop(0).wait()
        runtime.close()
        assert not multiprocessing.active_children()
        sender.close()


@pytest.mark.parametrize('policy', ['stop', 'continue'])
def testActualLoopbackSpawnReportsEncodingFailureFromWriter(policy):
    with tempfile.TemporaryDirectory(prefix='esq-') as root:
        context = multiprocessing.get_context('spawn')
        receive, send = context.Pipe(duplex=False)
        process = context.Process(target=encodingProbe, args=(root, policy, send))
        process.start()
        send.close()
        try:
            assert receive.poll(25), 'outer watchdog'
            result = receive.recv()
            process.join(5)
            assert 'error' not in result, result.get('error')
            assert not process.is_alive() and process.exitcode == 0
            assert result['receipt']['status'] == 'FAILED'
        finally:
            if process.is_alive():
                process.terminate()
                process.join(3)
            if process.is_alive():
                process.kill()
                process.join(3)
            receive.close()
            process.close()


def testUnexpectedBindingDeliveryErrorTerminatesItsSourceNode(tmp_path, monkeypatch):
    import emo_master.apps.runtime.workflow.runner as implementation
    path = makeDb(tmp_path / 'business.sqlite3')
    payload = project()
    payload['workflows']['main']['nodes'][0]['params'] = config(path)
    def unexpected(*_args):
        raise RuntimeError('injected unexpected delivery failure')
    monkeypatch.setattr(implementation, 'captureBoundValue', unexpected)
    registry = {OPERATOR_ID: SqliteWriterOperator}
    events = []
    runner = WorkflowRunner(WorkflowCompiler(registry).compile(payload), registry, eventPublisher=lambda **e: events.append(e))
    with pytest.raises(RuntimeError, match='unexpected delivery'):
        runner.run('main', {'value': 7}, RunContext.root('job', 'main'), CancellationToken())
    assert [e['eventType'] for e in events if e['context'].callerNodeId == 'input'] == ['node.started', 'node.failed']
    assert not any(e['eventType'] == 'sqlite.write.started' for e in events)
