import json
from dataclasses import replace

import pytest

from emo_master.apps.runtime.events.jsonl_writer import RuntimeJsonlLogWriter
from emo_master.apps.runtime.events.models import RuntimeEvent
from emo_master.apps.runtime.context.global_variables import ReadOnlyVariables
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from emo_master.apps.runtime.workflow.cancellation import CancellationToken
from emo_master.apps.runtime.workflow.context import RunContext
from emo_master.apps.runtime.workflow.runner import WorkflowExecutionError, WorkflowRunner
from emo_master.core.contracts.run_inspection import MAX_IO_BYTES, encodedBytes
from emo_master.core.log_export import exportLogs
from emo_master.core.plugin.models import PluginDescriptor
from emo_master.core.project.models import ProjectDocument, WorkflowEdge, WorkflowNode
from emo_master.core.workflow.compiler import WorkflowCompiler
from examples.flow_run_inspection import sampleProject


def execute(document, registry, inputs=None):
    events = []
    compiled = WorkflowCompiler(operatorRegistry=registry).compile(document)
    runner = WorkflowRunner(compiled, registry, eventPublisher=lambda **raw: events.append(raw))
    return runner, events


def item(summary, side, name):
    return next(row['value'] for row in summary[side]['items'] if row['port'] == name)


def testRealLocalImageCountCompareAndJsonlShareInvocation(tmp_path):
    service = RuntimeService(dbPath=tmp_path / "registry.db")
    try:
        registry = service.pluginScanResult.activeOperators
        document = sampleProject(tmp_path)
        operatorId = "vision.analysis.histogram"
        workflow = document.workflows["main"]
        workflow.nodes.append(WorkflowNode(nodeId="histogram", operatorId=operatorId))
        workflow.edges.append(WorkflowEdge(fromNode="load", fromPort="image", toNode="histogram", toPort="image"))
        runner, events = execute(document, registry)
    finally:
        service.close()
    runner.run('main', {}, RunContext.root('actual-job', 'main'), CancellationToken())
    done = {raw['context'].callerNodeId: raw for raw in events if raw['eventType'] == 'node.completed'}
    started = {raw['context'].callerNodeId: raw for raw in events if raw['eventType'] == 'node.started'}
    assert item(done['load']['payload']['ioSummary'], 'outputs', 'image')['shape'] == [240, 360, 3]
    assert item(done['count']['payload']['ioSummary'], 'inputs', 'blobs')['count'] == 2
    assert item(done['count']['payload']['ioSummary'], 'outputs', 'count')['value'] == 2
    assert item(done['presence']['payload']['ioSummary'], 'inputs', 'left')['value'] == 2
    assert item(done['presence']['payload']['ioSummary'], 'outputs', 'result')['value'] is True
    descriptor = registry[operatorId]
    assert descriptor.operatorClass.__base__.meta.version != descriptor.manifest.version
    for phase in (started, done):
        assert phase["histogram"]["payload"]["operatorId"] == operatorId
        assert phase["histogram"]["payload"]["operatorVersion"] == descriptor.manifest.version == "1.1.0"
        assert "operatorId" not in phase["input"]["payload"]
    for node in done:
        assert done[node]['context'].nodeRunId == started[node]['context'].nodeRunId
        assert encodedBytes(done[node]['payload']['ioSummary']) <= MAX_IO_BYTES
    assert (tmp_path / 'result.png').is_file()
    writer = RuntimeJsonlLogWriter(tmp_path / 'logs')
    try:
        for sequence, raw in enumerate(events, 1):
            ctx = raw['context']
            writer.enqueue(RuntimeEvent(jobId=ctx.jobId, eventType=raw['eventType'],
                message=raw['message'], payloadJson=json.dumps(raw['payload']),
                nodeId=ctx.callerNodeId, nodeRunId=ctx.nodeRunId, workflowId=ctx.workflowId,
                workflowRunId=ctx.workflowRunId, sequence=sequence, timestampMs=1800000000000))
    finally:
        writer.close()
    records = [json.loads(line) for path in (tmp_path / 'logs').glob('*.jsonl') for line in path.read_text(encoding='utf-8').splitlines()]
    count = next(row for row in records if row['eventType'] == 'node.completed' and row['nodeId'] == 'count')
    assert count['payload']['ioSummary'] == done['count']['payload']['ioSummary']
    assert 'outputs' not in count['payload']  # The original full-output projection remains.
    assert count['nodeRunId'] == done['count']['context'].nodeRunId


@pytest.mark.parametrize("scheme", ("rtsp", "rtsps"))
def testParameterFactsAreSafeFromRawEventThroughQueryAndExport(tmp_path, scheme):
    logDirectory = tmp_path / "logs"
    service = RuntimeService(dbPath=tmp_path / "runtime.db", logDirectory=logDirectory)
    configured = f"{scheme}://fixture-user:fixture-password@example.invalid/stream?fixture-opaque&access%5ftoken=fixture-token&mode=preview"
    safeUrl = f"{scheme}://<redacted>@example.invalid/stream?<redacted>&access%5ftoken=<redacted>&mode=preview"
    businessUrl = "https://example.invalid/report?report-name&mode=preview"

    class Probe:
        def executeNode(self, inputs, params, context):
            assert params["authName"] == "fixture-identity" and params["url"] == configured
            return {"status": "ok", "outputs": {"applied": inputs["value"]},
                    "diagnostics": {"endpoint": configured, "report": businessUrl, "tokenCount": 3,
                                    "session": {"leaseId": "fixture-lease"},
                                    "effectiveParameters": [{"path": ["value"], "source": "input", "value": inputs["value"]}]}}

    schema = {"type": "object", "properties": {
        "authName": {"type": "string", "xSensitive": True},
        "url": {"type": "string"},
        "value": {"type": "integer"},
        "mode": {"type": "string"},
        "unused": {"type": "string", "xValidateWhen": {"mode": ["other"]}},
        "absent": {"type": "string"},
    }}
    operatorId = "test.log_probe"
    manifest = replace(service.pluginScanResult.activeOperators["vision.value.number"].manifest,
                       operatorId=operatorId, entry="test:Probe", version="3.2.1", inputPorts={"value": "integer"},
                       outputPorts={"applied": "integer"}, paramSchema=schema,
                       resultSchema={"captureParams": [[key] for key in schema["properties"]],
                                     "diagnostics": {"effectiveParameters": {}}})
    registry = {operatorId: PluginDescriptor(manifest, Probe)}
    document = sampleProject(tmp_path).model_dump()
    document.update(schemaVersion="2.4", presentation={}, resources={}, production={},
                    globalVariables={"v": {"name": "Original", "type": "string", "initialValue": "fixture-identity"}})
    document["workflows"]["main"] = dict(name="Probe", inputs={"value": "integer"}, nodes=[
        {"nodeId": "input", "kind": "workflow_input"},
        {"nodeId": "probe", "operatorId": operatorId,
         "params": {"value": 0, "authName": "ignored-by-variable", "url": configured, "mode": "normal", "unused": "draft"},
         "globalVariableBindings": [{"variableId": "v", "parameterPath": ["authName"]}]},
        {"nodeId": "output", "kind": "workflow_output"},
    ], edges=[{"fromNode": "input", "fromPort": "value", "toNode": "probe", "toPort": "value"}])
    runtimeRecords = []

    def publish(*, eventType, context, message, level, code, payload):
        entry = service.eventStore.append(context.jobId, eventType, message, level=level, code=code,
            payload=payload, nodeId=context.callerNodeId, projectId=context.projectId, workflowId=context.workflowId,
            workflowRunId=context.workflowRunId, nodeRunId=context.nodeRunId)
        runtimeRecords.append(entry)

    try:
        compiled = WorkflowCompiler(operatorRegistry=registry).compile(document)
        runner = WorkflowRunner(compiled, registry, eventPublisher=publish,
                                globalVariables=ReadOnlyVariables({"v": "fixture-identity"}))
        runner.run("main", {"value": 8}, RunContext.root("job", "main"), CancellationToken())
        raw = next(row for row in runtimeRecords if row.nodeId == "probe" and row.eventType == "node.completed")
        rawPayload = json.loads(raw.payloadJson)
        expected = [
            {"path": ["authName"], "value": "<redacted>", "stage": "call", "source": "variable"},
            {"path": ["url"], "value": safeUrl, "stage": "call", "source": "prepared"},
            {"path": ["value"], "value": 0, "stage": "call", "source": "prepared"},
            {"path": ["mode"], "value": "normal", "stage": "call", "source": "prepared"},
            {"path": ["value"], "value": 8, "stage": "effective", "source": "input"},
        ]
        assert rawPayload["operatorId"] == operatorId and rawPayload["operatorVersion"] == manifest.version
        assert rawPayload["outputs"] == {"applied": 8}
        assert rawPayload["parameterSummary"] == expected
        assert rawPayload["diagnostics"] == {"endpoint": safeUrl, "report": businessUrl, "tokenCount": 3,
                                              "session": {"leaseId": "<redacted>"}}
        persisted = next(row for row in service.sqliteStore.listJobEventsAfter("job") if row.sequence == raw.sequence)
        assert persisted.payloadJson == raw.payloadJson

        reply = service.QueryRuntimeLogs(pb.QueryRuntimeLogsRequest(job_id="job", node_id="probe"), None)
        assert reply.ok
        row = next(row.event for row in reply.events if row.event.event_type == "node.completed")
        queried = json.loads(row.payload_json)
        assert queried["operatorId"] == operatorId and queried["operatorVersion"] == manifest.version
        assert queried["parameterSummary"] == expected and "outputs" not in queried
        destination = tmp_path / "export.jsonl"
        exportLogs(destination, [queried], formatName="jsonl", scope="runtime_history", filters={})
        exported = destination.read_text(encoding="utf-8")
        assert json.loads(exported) == queried
    finally:
        service.close()

    lines = [line for path in logDirectory.glob("*.jsonl") for line in path.read_text(encoding="utf-8").splitlines()]
    logged = next(json.loads(line) for line in lines
                  if json.loads(line)["eventType"] == "node.completed" and json.loads(line)["nodeId"] == "probe")
    assert logged["payload"] == queried
    for private in ("fixture-identity", "fixture-user", "fixture-password", "fixture-opaque", "fixture-token", "fixture-lease"):
        assert private not in raw.payloadJson + row.payload_json + exported + "\n".join(lines)


def testRunnerFreezesInputBeforeOperatorMutationAndKeepsFailedInput():
    class Mutating:
        def executeNode(self, inputs, params, context):
            inputs['items'].clear()
            if params.get('fail'):
                return {'status': 'failed', 'error': {'code': 'E_CONTROLLED', 'message': 'failed after mutation'}}
            return {'status': 'ok', 'outputs': {'count': len(inputs['items'])}}
    document = ProjectDocument.model_validate({
        'schemaVersion': '2.1', 'project': {'projectId': 'mutation', 'name': 'mutation',
            'createdAt': '2026-10-04T00:00:00Z', 'updatedAt': '2026-10-04T00:00:00Z'},
        'entryWorkflowId': 'main', 'workflowOrder': ['main'],
        'workflows': {'main': {'name': 'main', 'inputs': {'items': 'list'}, 'nodes': [
            {'nodeId': 'in', 'kind': 'workflow_input'},
            {'nodeId': 'mutate', 'operatorId': 'test.mutate', 'inputPorts': {'items': 'list'}, 'outputPorts': {'count': 'integer'}},
            {'nodeId': 'out', 'kind': 'workflow_output'},
        ], 'edges': [{'fromNode': 'in', 'fromPort': 'items', 'toNode': 'mutate', 'toPort': 'items'}]}},
    })
    runner, events = execute(document, {'test.mutate': Mutating})
    runner.run('main', {'items': [1, 2]}, RunContext.root('mutation-job', 'main'), CancellationToken())
    done = next(row for row in events if row['eventType'] == 'node.completed' and row['context'].callerNodeId == 'mutate')
    assert item(done['payload']['ioSummary'], 'inputs', 'items')['count'] == 2
    assert item(done['payload']['ioSummary'], 'outputs', 'count')['value'] == 0
    document.workflows['main'].nodes[1].params['fail'] = True
    runner, events = execute(document, {'test.mutate': Mutating})
    with pytest.raises(WorkflowExecutionError, match='failed after mutation'):
        runner.run('main', {'items': [1, 2, 3]}, RunContext.root('failed-job', 'main'), CancellationToken())
    failed = next(row for row in events if row['eventType'] == 'node.failed')
    assert item(failed['payload']['ioSummary'], 'inputs', 'items')['count'] == 3
    assert failed['payload']['ioSummary']['outputs'] is None


def testRunnerSkippedInvocationReportsMissingUpstreamAndNoOutputs():
    class Optional:
        def executeNode(self, inputs, params, context):
            return {'status': 'ok', 'outputs': {}}
    class MustSkip:
        def executeNode(self, inputs, params, context):
            raise AssertionError('missing required input must not run')
    document = ProjectDocument.model_validate({
        'schemaVersion': '2.1', 'project': {'projectId': 'skip', 'name': 'skip',
            'createdAt': '2026-10-04T00:00:00Z', 'updatedAt': '2026-10-04T00:00:00Z'},
        'entryWorkflowId': 'main', 'workflowOrder': ['main'],
        'workflows': {'main': {'name': 'main', 'nodes': [
            {'nodeId': 'in', 'kind': 'workflow_input'},
            {'nodeId': 'optional', 'operatorId': 'test.optional', 'outputPorts': {'value': 'object'}},
            {'nodeId': 'skip', 'operatorId': 'test.skip', 'inputPorts': {'value': 'object'}},
            {'nodeId': 'out', 'kind': 'workflow_output'},
        ], 'edges': [{'fromNode': 'optional', 'fromPort': 'value', 'toNode': 'skip', 'toPort': 'value'}]}},
    })
    runner, events = execute(document, {'test.optional': Optional, 'test.skip': MustSkip})
    runner.run('main', {}, RunContext.root('skip-job', 'main'), CancellationToken())
    skipped = next(raw for raw in events if raw['eventType'] == 'node.skipped')
    assert skipped['payload']['code'] == 'E_INPUT_NOT_PRODUCED'
    assert skipped['payload']['ioSummary']['outputs'] is None
    assert skipped['payload']['ioSummary']['inputs']['portCount'] == 0
    assert skipped['context'].nodeRunId
