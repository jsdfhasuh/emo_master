"""G2/F6 representative advanced edits through the actual public HTTP routes.

Declarations use SourceCatalog, explicitly a source fixture; this is not a
live Runtime/device acceptance test or a second end-to-end image delivery run.
"""
from copy import deepcopy

from fastapi.testclient import TestClient
import pytest

from emo_master.apps.designer.state.project_store import loadProjectDocument
from emo_master.authoring.http import create_app
from emo_master.authoring.service import AuthoringService
from tests.authoring.test_drafts import SourceCatalog


@pytest.fixture
def public(tmp_path):
    declarations = SourceCatalog()
    declarations.targets = {}  # Source fixture has no live configured Runtime.
    business = AuthoringService(tmp_path, catalog=declarations)
    app = create_app(tmp_path, 'public-advanced-test-credential', service=business, enable_mcp=False)
    with TestClient(app, headers={'Authorization': 'Bearer public-advanced-test-credential'}) as client:
        yield client, tmp_path


def _create(client):
    reply = client.post('/api/project-drafts', json={'name': 'Advanced public commands'})
    assert reply.status_code == 200, reply.text
    return reply.json()


def _action(client, state, action, **fields):
    reply = client.post('/api/project-drafts/' + state['draftId'] + '/' + action,
                        json={'expectedDraftRevision': state['draftRevision'], **fields})
    assert reply.status_code == 200, reply.text
    return reply.json()


def _edit(client, state, command_type, **fields):
    return _action(client, state, 'commands', command={'type': command_type, **fields})


def _node(state, node_id):
    return next(node for node in state['project']['workflows']['main']['nodes'] if node['nodeId'] == node_id)


def test_advanced_commands_from_http_preserve_full_project_and_save_reload(public):
    client, root = public
    caps = client.get('/api/authoring-capabilities')
    assert caps.status_code == 200, caps.text
    schema = next(tool['inputSchema'] for tool in caps.json()['tools'] if tool['name'] == 'apply_project_command')
    assert {'Component', 'DeviceBinding', 'VariableDefinition'} <= set(schema['$defs'])
    state = _create(client)
    state = _edit(client, state, 'workflow.create', workflowId='body', name='Body')
    state = _edit(client, state, 'workflow.interface', workflowId='body', inputs={'value': 'number'}, outputs={'value': 'number'})
    state = _edit(client, state, 'control.create', workflowId='main', kind='subflow', nodeId='call',
                  config={'targetWorkflowId': 'body'})
    assert _node(state, 'call')['inputPorts'] == {'value': 'number'}
    state = _edit(client, state, 'control.create', workflowId='main', kind='loop', nodeId='repeat', config={
        'mode': 'repeat', 'bodyWorkflowId': 'body', 'repeatCount': 2, 'maxIterations': 8, 'timeoutMs': 1000})
    assert _node(state, 'repeat')['loop']['contractVersion'] == 2
    state = _edit(client, state, 'control.create', workflowId='main', kind='dispatch', nodeId='dispatch',
        inputPorts={'source': 'number'}, config={'queueFullPolicy': 'wait', 'waitTimeoutMs': 40,
        'targets': [{'targetSlotId': 'worker', 'workflowId': 'body', 'inputs': {'value': 'source'}, 'queueCapacity': 4}]})
    state = _edit(client, state, 'control.create', workflowId='main', kind='join', nodeId='join',
                  config={'dispatchNodeId': 'dispatch', 'timeoutMs': 1000})
    assert _node(state, 'join')['outputPorts'] == {'worker': 'object'}
    state = _edit(client, state, 'workflow.copy', workflowId='body')
    copied = state['createdIds']['workflowId']
    state = _edit(client, state, 'workflow.delete', workflowId=copied)
    assert state['project']['workflowOrder'] == ['main', 'body']

    state = _edit(client, state, 'variables.configure', definitions={
        'limit': {'name': 'Limit', 'type': 'integer', 'initialValue': 127}})
    state = _edit(client, state, 'node.create', workflowId='main', targetId='source-fixture',
                  operatorId='vision.preprocess.threshold', nodeId='threshold')
    binding = [{'parameterPath': ['threshold'], 'variableId': 'limit'}]
    state = _edit(client, state, 'node.configure', workflowId='main', nodeId='threshold', params={'mode': 'fixed'},
                  globalVariableBindings=binding)
    state = _edit(client, state, 'node.configure', workflowId='main', nodeId='threshold', params={'mode': 'otsu'})
    assert _node(state, 'threshold')['globalVariableBindings'] == binding

    declaration = client.get('/api/project-catalog/source-fixture/device-types/device.example.simulated_camera')
    assert declaration.status_code == 200, declaration.text
    device = declaration.json()['declaration']
    state = _edit(client, state, 'device.put', targetId='source-fixture', deviceId='camera', definition={
        'name': 'Disabled source fixture camera', 'deviceTypeId': device['deviceTypeId'], 'driverVersion': device['version'],
        'enabled': False, 'connection': {'source': 'offline'},
        'defaults': {'exposureMode': 'manual', 'exposureUs': 8000., 'gain': 0., 'enabled': False}})
    state = _edit(client, state, 'node.create', workflowId='main', targetId='source-fixture',
                  operatorId='fixture.camera', nodeId='camera-node')
    state = _edit(client, state, 'node.devices', workflowId='main', nodeId='camera-node', targetId='source-fixture',
                  deviceBindings={'camera': {'deviceId': 'camera'}})
    assert _node(state, 'camera-node')['deviceBindings']['camera']['deviceId'] == 'camera'

    state = _edit(client, state, 'page.create', name='Overview')
    overview = state['createdIds']['pageId']
    state = _edit(client, state, 'page.create', name='Detail')
    detail = state['createdIds']['pageId']
    detail_page = deepcopy(state['project']['presentation']['pages'][detail])
    detail_page['targets'] = ['desktop', 'web']
    state = _edit(client, state, 'page.configure', pageId=detail, page=detail_page)
    state = _edit(client, state, 'component.put', pageId=overview, component={
        'componentId': 'group', 'type': 'container', 'grid': {'type': 'responsive', 'direction': 'vertical'},
        'children': [{'componentId': 'title', 'type': 'text', 'props': {'text': 'Ready'}}]})
    state = _edit(client, state, 'component.put', pageId=overview, component={
        'componentId': 'to-detail', 'type': 'navigation_button', 'props': {'text': 'Open details'},
        'actions': {'clicked': {'type': 'navigate', 'pageId': detail}}})
    page = deepcopy(state['project']['presentation']['pages'][overview])
    page.update(targets=['desktop', 'web'], canvasTop=-120,
                layout={'type': 'responsive', 'direction': 'wrap', 'spacing': 16, 'alignment': 'center'})
    state = _edit(client, state, 'page.configure', pageId=overview, page=page)
    presentation = deepcopy(state['project']['presentation'])
    presentation['navigation'] = {'mode': 'buttons'}
    state = _edit(client, state, 'presentation.configure', presentation=presentation)
    state = _edit(client, state, 'page.default', pageId=overview)
    state = _edit(client, state, 'page.order', pageOrder=[detail, overview])
    state = _edit(client, state, 'production.configure', settings={
        'autoStart': False, 'mode': 'continuous', 'cycleIntervalMs': 500, 'inputs': {}})
    state = _edit(client, state, 'runtime.configure', settings={
        'maxConcurrentJobs': 4, 'startupWorkflowIds': ['main', 'body']})

    state = _edit(client, state, 'node.create', workflowId='main', targetId='source-fixture',
                  operatorId='vision.preprocess.roi', nodeId='roi')
    before_roi = deepcopy(state['project'])
    roi = {'roiType': 'regions', 'regions': [
        {'name': 'keep', 'purpose': 'keep', 'enabled': True, 'roiType': 'polygon',
         'points': [{'x': 1., 'y': 2.}, {'x': 9., 'y': 2.}, {'x': 9., 'y': 9.}]},
        {'name': 'exclude', 'purpose': 'exclude', 'enabled': False, 'roiType': 'bbox',
         'x': 2., 'y': 3., 'width': 2., 'height': 3.}]}
    state = _edit(client, state, 'node.configure', workflowId='main', nodeId='roi', params=roi)
    assert _node(state, 'roi')['params'] == roi
    configured = deepcopy(state['project'])
    state = _action(client, state, 'undo')
    assert state['project'] == before_roi
    state = _action(client, state, 'redo')
    assert state['project'] == configured
    state = _action(client, state, 'save')
    assert not state['dirty']
    read = client.get('/api/project-drafts/' + state['draftId'])
    assert read.status_code == 200 and read.json()['project'] == state['project']
    document = loadProjectDocument(root / 'project-drafts' / state['draftId'] / 'project.emoproj')
    assert document.model_dump(mode='json') == state['project']
    assert document.schemaVersion == '2.9' and document.presentation.schemaVersion == '1.2'
    assert document.presentation.pages[overview].targets == ['desktop', 'web']
    assert document.presentation.pages[overview].components[1].actions['clicked'].pageId == detail
    assert document.presentation.navigation.mode == 'buttons'
    assert document.production.mode == 'continuous' and document.runtime.maxConcurrentJobs == 4
    assert document.globalVariables['limit'].initialValue == 127
    assert document.devices.definitions['camera'].enabled is False


def test_public_promoted_model_schema_rejects_invalid_nested_input_without_edit(public):
    client, _ = public
    state = _create(client)
    before = client.get('/api/project-drafts/' + state['draftId']).json()
    invalid = [
        {'type': 'variables.configure', 'definitions': {'flag': {'name': 'Flag', 'type': 'not-a-variable-type', 'initialValue': True}}},
        {'type': 'node.devices', 'workflowId': 'main', 'nodeId': 'absent', 'targetId': 'source-fixture',
         'deviceBindings': {'camera': {'deviceId': 123}}},
        {'type': 'component.put', 'pageId': 'absent', 'component': {'componentId': 'bad', 'type': 'unknown-component'}},
        {'type': 'production.configure', 'settings': {'mode': 'not-a-production-mode'}},
    ]
    for command in invalid:
        response = client.post('/api/project-drafts/' + state['draftId'] + '/commands',
            json={'expectedDraftRevision': state['draftRevision'], 'command': command})
        assert response.status_code == 400, response.text
        error = response.json()
        assert error['code'] == 'E_INPUT' and error['fieldPath'].startswith('command')
        assert client.get('/api/project-drafts/' + state['draftId']).json() == before
