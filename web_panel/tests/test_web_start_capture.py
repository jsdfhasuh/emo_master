"""Independent Web starts use saved external requests and preserve Qt validation."""
import asyncio
import contextlib
import json
import os
import sys
import time
import unittest

import test_product_runtime as product_runtime

ROOT = product_runtime.ROOT


def run_independent_capture_server(root):
    # Application/Qt imports belong only to the separate owning Runtime process.
    sys.path.insert(0, str(ROOT))
    from scripts.independent_web_editor_demo import create_demo_project
    from emo_master.apps.runtime.grpc_server.service import RuntimeService
    from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
    from emo_master.apps.runtime.main import createRuntimeServer
    from emo_master.apps.designer.state.project_store import saveProject, loadProjectDocument
    from emo_master.core.project.models import ProjectDocument
    from emo_master.core.workflow.compiler import WorkflowCompiler

    with contextlib.redirect_stdout(sys.stderr):
        service = RuntimeService(dbPath=root / 'runtime.db', workspaceRoot=root / 'jobs')
        path, document = create_demo_project(root / 'project', service.pluginScanResult.activeOperators, continuous=False)
        payload = document.model_dump(mode='json')
        payload['presentation'].update(defaultPageId='qt', pageOrder=['qt'],
            resultScopes={'qt-root': {'entryWorkflowId': 'main', 'scopeWorkflowId': 'main'}},
            dataSources={'stale-qt': {'kind': 'node_output', 'resultScopeId': 'qt-root',
                'workflowId': 'main', 'nodeId': 'deleted-node', 'port': 'count', 'expectedType': 'integer'}},
            pages={'qt': {'name': 'Qt deleted binding', 'targets': ['desktop'],
                'components': [{'componentId': 'qt-number', 'type': 'number', 'bindings': {'value': 'stale-qt'}}]}})
        document = ProjectDocument.model_validate(payload)
        assert document.schemaVersion == '2.9' and document.presentation.schemaVersion == '1.2'
        saveProject(path, document.model_dump(mode='json'))
        restored = loadProjectDocument(path)
        assert restored == document
        WorkflowCompiler(operatorRegistry=service.pluginScanResult.activeOperators).compile(restored)
        (root / 'fixture-checks.json').write_text(json.dumps({
            'schemaVersion': document.schemaVersion, 'presentationVersion': document.presentation.schemaVersion,
            'generatedBy': 'WorkflowStore.toPayload via create_demo_project',
            'savedReloadedEqual': True, 'compiled': True, 'qtDeletedNode': 'deleted-node',
            'devices': list(document.devices.definitions)}, indent=2), encoding='utf-8')
        loaded = service.LoadProject(pb.LoadProjectRequest(project_path=str(path)), None)
        if not loaded.ok:
            service.close()
            raise RuntimeError(loaded.message)
        server, port, _ = createRuntimeServer(port=0, runtimeService=service)
        server.start()
    try:
        print(json.dumps({'event': 'ready', 'address': f'127.0.0.1:{port}'}), flush=True)
        while not (root / 'stop-requested').exists():
            time.sleep(.05)
    finally:
        with contextlib.redirect_stdout(sys.stderr):
            server.stop(0).wait()
            service.close()


@unittest.skipUnless(os.environ.get('EMO_WEB_PRODUCT_RUNTIME') == '1',
                     'Opt-in full owning Runtime integration; requires its separate dependencies')
class IndependentWebCaptureTests(unittest.IsolatedAsyncioTestCase):
    server_flag = '--runtime-server-independent-web'
    asyncSetUp = product_runtime.ProductRuntimeTests.asyncSetUp
    asyncTearDown = product_runtime.ProductRuntimeTests.asyncTearDown
    preserve_evidence = product_runtime.ProductRuntimeTests.preserve_evidence
    reconcile_start = product_runtime.ProductRuntimeTests.reconcile_start

    async def test_web_start_uses_valid_external_request_despite_deleted_qt_source(self):
        info = await self.client.info()
        catalog = await self.client.output_catalog()
        count = next(item for item in catalog['choices'] if item['source']['nodeId'] == 'count'
                     and item['source']['port'] == 'count' and not item['source']['fieldPath'])
        current = await self.client.output_requests()
        config = {'schemaVersion': '1.0', 'sources': {count['sourceId']: count['source']},
                  'scopes': {count['scopeId']: count['scope']}}
        saved = await self.client.submit_output_requests({
            'runtimeInstanceId': info['runtimeInstanceId'], 'projectId': info['projectId'],
            'expectedDocumentRevision': info['documentRevision'], 'expectedConfigRevision': current['configRevision'],
            'requestId': 'web-capture-config-1', 'config': config})
        self.observations.append({'operation': 'save_external_count', 'reply': saved})
        self.assertTrue(saved['ok'], saved)
        body = {'runtimeInstanceId': info['runtimeInstanceId'], 'projectId': info['projectId'],
                'configurationRevision': info['configurationRevision'], 'workflowId': 'main',
                'requestId': 'web-with-stale-qt-1', 'inputs': {}}
        web = await self.client.control('start', 'main', body)
        web = await self.reconcile_start(web, body)
        self.assertEqual(web['outcome'], 'ACCEPTED', web)
        result = []
        for _ in range(200):
            result = [item for item in await self.client.snapshot(web['runId']) if item.get('type') != 'scope_status']
            if result:
                break
            await asyncio.sleep(.05)
        self.observations.append({'operation': 'external_count_result', 'reply': result})
        self.assertTrue(result, 'Actual DisplayService never produced count.count')
        self.assertEqual(result[0]['sources'][count['sourceId']]['value'], 2)
        qt = await self.client.runtime.StartJob(self.client.pb.StartJobRequest(
            project_id=info['projectId'], workflow_id='main', capture_presentation=True,
            start_request_id='qt-with-stale-qt-1', expected_runtime_instance_id=info['runtimeInstanceId'],
            expected_document_revision=int(info['documentRevision'])), timeout=5)
        self.observations.append({'operation': 'explicit_qt_start', 'ok': qt.ok, 'message': qt.message})
        self.assertFalse(qt.ok, 'Explicit Qt capture must retain its deleted binding validation')
        self.assertIn('stale-qt', qt.message)
