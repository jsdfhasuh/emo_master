import json

import pytest

from emo_master.apps.runtime.events.jsonl_writer import RuntimeJsonlLogWriter
from emo_master.apps.runtime.events.models import RuntimeEvent
from emo_master.apps.runtime.workflow.cancellation import CancellationToken
from emo_master.apps.runtime.workflow.context import RunContext
from emo_master.apps.runtime.workflow.runner import WorkflowExecutionError, WorkflowRunner
from emo_master.core.contracts.run_inspection import MAX_IO_BYTES, encodedBytes
from emo_master.core.project.models import ProjectDocument
from emo_master.core.workflow.compiler import WorkflowCompiler
from emo_master.plugins.builtins.blob_analysis.operator import BlobAnalysisOperator
from emo_master.plugins.builtins.collection_count.operator import CollectionCountOperator
from emo_master.plugins.builtins.image_loader.operator import ImageLoaderOperator
from emo_master.plugins.builtins.image_saver.operator import ImageSaverOperator
from emo_master.plugins.builtins.number_compare.operator import NumberCompareOperator
from examples.flow_run_inspection import sampleProject


def execute(document, registry, inputs=None):
    events = []
    compiled = WorkflowCompiler(operatorRegistry=registry).compile(document)
    runner = WorkflowRunner(compiled, registry, eventPublisher=lambda **raw: events.append(raw))
    return runner, events


def item(summary, side, name):
    return next(row['value'] for row in summary[side]['items'] if row['port'] == name)


def testRealLocalImageCountCompareAndJsonlShareInvocation(tmp_path):
    registry = {value.meta.operatorId: value for value in (
        ImageLoaderOperator, BlobAnalysisOperator, CollectionCountOperator,
        NumberCompareOperator, ImageSaverOperator)}
    runner, events = execute(sampleProject(tmp_path), registry)
    runner.run('main', {}, RunContext.root('actual-job', 'main'), CancellationToken())
    done = {raw['context'].callerNodeId: raw for raw in events if raw['eventType'] == 'node.completed'}
    started = {raw['context'].callerNodeId: raw for raw in events if raw['eventType'] == 'node.started'}
    assert item(done['load']['payload']['ioSummary'], 'outputs', 'image')['shape'] == [240, 360, 3]
    assert item(done['count']['payload']['ioSummary'], 'inputs', 'blobs')['count'] == 2
    assert item(done['count']['payload']['ioSummary'], 'outputs', 'count')['value'] == 2
    assert item(done['presence']['payload']['ioSummary'], 'inputs', 'left')['value'] == 2
    assert item(done['presence']['payload']['ioSummary'], 'outputs', 'result')['value'] is True
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
