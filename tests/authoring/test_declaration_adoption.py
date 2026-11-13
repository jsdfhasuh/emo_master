"""F3/F4 public recovery with real PNG bytes and explicit declaration sources."""
from copy import deepcopy
from datetime import datetime, timezone
import asyncio
import io

from fastapi.testclient import TestClient
from PIL import Image
import pytest

from emo_master.apps.designer.state.project_store import loadProjectDocument
from emo_master.authoring.errors import AuthoringError
from emo_master.authoring.http import create_app
from emo_master.authoring.service import AuthoringService
from tests.authoring.test_drafts import SourceCatalog
from tests.authoring.test_transport import HEADERS, TOKEN
from tests.authoring.test_transport import real_target as real_target


def edit(client, state, kind, **fields):
    return action(client, state, 'commands', command={'type': kind, **fields})


def action(client, state, endpoint, **fields):
    reply = client.post('/api/project-drafts/' + state['draftId'] + '/' + endpoint,
                        json={'expectedDraftRevision': state['draftRevision'], **fields})
    assert reply.status_code == 200, reply.text
    return reply.json()


def create_image(client):
    response = client.post('/api/project-drafts', json={'name': 'PNG recovery'})
    assert response.status_code == 200, response.text
    state = edit(client, response.json(), 'node.create', workflowId='main', targetId='local',
                 operatorId='vision.io.image_loader', nodeId='image')
    content = io.BytesIO()
    Image.new('RGB', (4, 3), (60, 120, 180)).save(content, format='PNG')
    png = content.getvalue()
    uploaded = client.post('/api/project-drafts/' + state['draftId'] + '/resources?name=input.png&purpose=input_image',
        content=png, headers={'Content-Type': 'application/octet-stream',
                              'X-Expected-Draft-Revision': state['draftRevision']})
    assert uploaded.status_code == 200, uploaded.text
    state['draftRevision'] = uploaded.json()['draftRevision']
    state = edit(client, state, 'resource.bind', resourceId=uploaded.json()['resourceId'],
                 target={'workflowId': 'main', 'nodeId': 'image', 'parameterPath': ['imagePath']})
    return state, uploaded.json()['resourceId'], png


def test_public_copy_preserves_resource_site_bindings_bytes_and_single_history(tmp_path, real_target):
    target, _runtime = real_target
    root = tmp_path / 'authoring'
    service = AuthoringService(root, {'local': target})
    with TestClient(create_app(root, TOKEN, service=service, enable_mcp=False), headers=HEADERS) as client:
        state, resource_id, png = create_image(client)
        before = deepcopy(state['project'])
        draft = service.drafts.get_draft(state['draftId'])
        paths = list((draft.directory / 'assets').rglob('*'))
        history = len(draft.session._undo)
        state = edit(client, state, 'node.copy', workflowId='main', nodeId='image')
        copied_id = state['createdIds']['nodeId']
        bindings = state['project']['resources']['parameterBindings']
        assert bindings[0] == before['resources']['parameterBindings'][0]
        assert bindings[1] == {'target': {'workflowId': 'main', 'nodeId': copied_id, 'parameterPath': ['imagePath']},
                               'resourceId': resource_id}
        assert state['project']['resources']['items'] == before['resources']['items']
        assert list((draft.directory / 'assets').rglob('*')) == paths
        assert len(draft.session._undo) == history + 1
        assert (draft.directory / state['project']['resources']['items'][resource_id]['path']).read_bytes() == png
        assert action(client, state, 'validate')['status'] == 'PASSED'
        copied_project = deepcopy(state['project'])
        state = action(client, state, 'undo')
        assert state['project'] == before
        state = action(client, state, 'redo')
        assert state['project'] == copied_project
        state = edit(client, state, 'node.create', workflowId='main', targetId='local', operatorId='vision.io.image_saver',
                     nodeId='save', params={'outputPath': 'outputs/result.png'})
        state = edit(client, state, 'site.bind', name='result-file', purpose='output_file',
                     target={'workflowId': 'main', 'nodeId': 'save', 'parameterPath': ['outputPath']})
        site_before = deepcopy(state['project'])
        state = edit(client, state, 'node.copy', workflowId='main', nodeId='save')
        copied_save = state['createdIds']['nodeId']
        sites = state['project']['resources']['siteBindings']
        assert sites[0] == site_before['resources']['siteBindings'][0]
        assert sites[1] == {**sites[0], 'target': {**sites[0]['target'], 'nodeId': copied_save}}
        site_copy = deepcopy(state['project'])
        state = action(client, state, 'undo')
        assert state['project'] == site_before
        state = action(client, state, 'redo')
        assert state['project'] == site_copy
        for saver in ('save', copied_save):
            state = edit(client, state, 'edge.connect', workflowId='main', fromNode='image', fromPort='image',
                         toNode=saver, toPort='image')
        assert action(client, state, 'validate')['status'] == 'PASSED'
        state = action(client, state, 'save')
        assert loadProjectDocument(draft.directory / 'project.emoproj').model_dump(mode='json') == state['project']


@pytest.mark.parametrize('origin', ['restart', 'zip-import'])
def test_live_public_declaration_adoption_after_restart_or_zip_import(tmp_path, real_target, origin):
    target, runtime = real_target
    root = tmp_path / 'authoring'
    service = AuthoringService(root, {'local': target})
    with TestClient(create_app(root, TOKEN, service=service, enable_mcp=False), headers=HEADERS) as client:
        state, _resource_id, _png = create_image(client)
        state = edit(client, state, 'node.create', workflowId='main', targetId='local',
                     operatorId='vision.preprocess.threshold', nodeId='threshold')
        state = edit(client, state, 'edge.connect', workflowId='main', fromNode='image', fromPort='image',
                     toNode='threshold', toPort='image')
        state = action(client, state, 'save')
        if origin == 'zip-import':
            exported = action(client, state, 'exports', format='project-zip')
            downloaded = client.get(exported['download']['path'])
            assert downloaded.status_code == 200
            imported = client.post('/api/project-drafts/imports?filename=project.zip', content=downloaded.content,
                                   headers={'Content-Type': 'application/octet-stream'})
            assert imported.status_code == 200, imported.text
            state = imported.json()
    restarted = AuthoringService(root, {'local': target})
    with TestClient(create_app(root, TOKEN, service=restarted, enable_mcp=False), headers=HEADERS) as client:
        state = client.get('/api/project-drafts/' + state['draftId']).json()
        baseline = deepcopy(state)
        draft = restarted.drafts.get_draft(state['draftId'])
        assert action(client, state, 'validate')['status'] == 'INCOMPLETE'
        outputs = client.get('/api/project-drafts/' + state['draftId'] + '/output-choices').json()['outputs']
        assert any(item['issues'] for item in outputs if item['nodeId'] == 'threshold')
        capabilities = client.get('/api/authoring-capabilities').json()
        tool = next(tool for tool in capabilities['tools'] if tool['name'] == 'adopt_project_declarations')
        assert tool['inputSchema']['required'] == ['draftId', 'expectedDraftRevision', 'targetId']
        assert not tool['annotations']['readOnlyHint'] and tool['annotations']['openWorldHint']
        state = action(client, state, 'declarations', targetId='local')
        assert state['changed'] and state['draftRevision'] != baseline['draftRevision']
        assert state['project'] == baseline['project'] and state['dirty'] == baseline['dirty']
        assert not draft.session._undo and not draft.session._redo
        assert state['declarationContext']['runtimeInstanceId'] == runtime.runtimeInstanceId
        assert action(client, state, 'validate')['status'] == 'PASSED'
        outputs = client.get('/api/project-drafts/' + state['draftId'] + '/output-choices').json()['outputs']
        mask = next(item for item in outputs if item['nodeId'] == 'threshold' and item['port'] == 'mask')
        assert mask['issues'] == [] and mask['spec']['required'] is True
        repeat = action(client, state, 'declarations', targetId='local')
        assert not repeat['changed'] and repeat['draftRevision'] == state['draftRevision']
        stale = client.post('/api/project-drafts/' + state['draftId'] + '/commands', json={
            'expectedDraftRevision': baseline['draftRevision'], 'command': {'type': 'project.rename', 'name': 'stale'}})
        assert stale.status_code == 409
        state = edit(client, state, 'node.configure', workflowId='main', nodeId='threshold', params={'mode': 'otsu'})
        assert action(client, state, 'validate')['status'] == 'PASSED'
        state = action(client, state, 'save')
        assert loadProjectDocument(draft.directory / 'project.emoproj').model_dump(mode='json') == state['project']


class SourceDeclarations(SourceCatalog):
    """Explicit source fixture with controllable availability, not a live target."""
    targets = {}
    changed_version = False
    changed_device_version = False
    offline = False

    def list_operators(self, target_id):
        if self.offline:
            raise AuthoringError('E_TARGET_UNAVAILABLE', 'Source fixture offline', 502)
        return {'targetId': target_id, 'runtimeInstanceId': None, 'operators': []}

    def get_operator(self, target_id, operator_id):
        value = super().get_operator('source-fixture', operator_id)
        value.setdefault('deviceRequirements', {})
        value.update(targetId=target_id, observedAt=datetime.now(timezone.utc).isoformat())
        if self.changed_version:
            value['version'] = 'incompatible-fixture-version'
        return value

    def get_device_type(self, target_id, type_id):
        value = super().get_device_type('source-fixture', type_id)
        if self.changed_device_version:
            value['declaration']['version'] = '9.9.9'
        return {**value, 'targetId': target_id}


def test_public_device_adoption_and_version_offline_failures_preserve_content(tmp_path):
    catalog = SourceDeclarations()
    original = AuthoringService(tmp_path, catalog=catalog)
    with TestClient(create_app(tmp_path, TOKEN, service=original, enable_mcp=False), headers=HEADERS) as client:
        state = client.post('/api/project-drafts', json={}).json()
        device = catalog.get_device_type('source-fixture', 'device.example.simulated_camera')['declaration']
        state = edit(client, state, 'device.put', targetId='source-fixture', deviceId='camera', definition={
            'name': 'Offline camera', 'deviceTypeId': device['deviceTypeId'], 'driverVersion': device['version'],
            'connection': {'source': 'offline'}, 'defaults': {'exposureMode': 'manual', 'exposureUs': 8000., 'gain': 0., 'enabled': False}})
        state = edit(client, state, 'node.create', workflowId='main', targetId='source-fixture', operatorId='fixture.camera', nodeId='camera-node')
        state = edit(client, state, 'node.devices', workflowId='main', nodeId='camera-node', targetId='source-fixture',
                     deviceBindings={'camera': {'deviceId': 'camera'}})
        state = action(client, state, 'save')
    restored = AuthoringService(tmp_path, catalog=catalog)
    with TestClient(create_app(tmp_path, TOKEN, service=restored, enable_mcp=False), headers=HEADERS) as client:
        state = client.get('/api/project-drafts/' + state['draftId']).json()
        catalog.changed_version = True
        mismatch = client.post('/api/project-drafts/' + state['draftId'] + '/declarations', json={
            'expectedDraftRevision': state['draftRevision'], 'targetId': 'source-fixture'})
        assert mismatch.status_code == 409 and mismatch.json()['code'] == 'E_OPERATOR_DEPENDENCY'
        assert mismatch.json()['diagnostics'][0]['fieldPath'] == 'dependencies.operators.fixture.camera'
        assert not restored.drafts.get_draft(state['draftId']).operators
        assert action(client, state, 'validate')['status'] == 'INCOMPLETE'
        catalog.changed_version = False
        catalog.changed_device_version = True
        mismatch_device = client.post('/api/project-drafts/' + state['draftId'] + '/declarations', json={
            'expectedDraftRevision': state['draftRevision'], 'targetId': 'source-fixture'})
        assert mismatch_device.status_code == 409 and mismatch_device.json()['code'] == 'E_DEVICE_DEPENDENCY'
        assert mismatch_device.json()['diagnostics'][0]['fieldPath'] == 'devices.definitions.camera.driverVersion'
        assert not restored.drafts.get_draft(state['draftId']).operators
        assert not restored.drafts.get_draft(state['draftId']).device_types
        catalog.changed_device_version = False
        catalog.offline = True
        offline = client.post('/api/project-drafts/' + state['draftId'] + '/declarations', json={
            'expectedDraftRevision': state['draftRevision'], 'targetId': 'source-fixture'})
        assert offline.status_code == 502 and offline.json()['code'] == 'E_TARGET_UNAVAILABLE'
        assert client.get('/api/project-drafts/' + state['draftId']).json() == state
        state = action(client, state, 'save')
        catalog.offline = False
        before = deepcopy(state)
        state = action(client, state, 'declarations', targetId='source-fixture')
        assert state['project'] == before['project'] and state['dirty'] == before['dirty']
        assert state['declarationContext']['runtimeInstanceId'] is None
        assert len(restored.drafts.get_draft(state['draftId']).device_types) == 1
        assert action(client, state, 'validate')['status'] == 'PASSED'
        repeated = action(client, state, 'declarations', targetId='source-fixture')
        assert not repeated['changed'] and repeated['draftRevision'] == state['draftRevision']
        state = edit(client, state, 'node.devices', workflowId='main', nodeId='camera-node', targetId='source-fixture',
                     deviceBindings={'camera': {'deviceId': 'camera'}}, deviceOverrides={'camera': {'gain': 1.}})
        assert action(client, state, 'validate')['status'] == 'PASSED'
        assert len(restored.drafts.get_draft(state['draftId']).session._undo) == 1
        before_context = deepcopy(state)
        state = action(client, state, 'declarations', targetId='equivalent-source-fixture')
        assert state['changed'] and state['draftRevision'] != before_context['draftRevision']
        assert state['project'] == before_context['project'] and state['dirty'] == before_context['dirty']
        assert len(restored.drafts.get_draft(state['draftId']).session._undo) == 1
        state = action(client, state, 'undo')
        assert not state['project']['workflows']['main']['nodes'][-1].get('deviceOverrides')


def test_public_adoption_requires_saved_exact_lock_and_keeps_draft_saveable(tmp_path):
    catalog = SourceDeclarations()
    service = AuthoringService(tmp_path, catalog=catalog)
    payload = service.drafts.create()['project']
    declaration = catalog.get_operator('source-fixture', 'vision.io.image_loader')
    payload['workflows']['main']['nodes'].append({'nodeId': 'unlocked', 'kind': 'operator',
        'operatorId': declaration['operatorId'], 'inputPorts': {}, 'outputPorts': {'image': 'image'},
        'paramSchema': declaration['paramSchema'], 'params': {}})
    state = asyncio.run(service.invoke('import_project_draft', {'project': payload}))
    before = service.drafts.read(state['draftId'])
    with pytest.raises(AuthoringError) as caught:
        asyncio.run(service.invoke('adopt_project_declarations', {'draftId': state['draftId'],
            'expectedDraftRevision': state['draftRevision'], 'targetId': 'source-fixture'}))
    assert caught.value.code == 'E_DECLARATION_VERSION_REQUIRED'
    issue = caught.value.details['diagnostics'][0]
    assert issue['nodeId'] == 'unlocked' and issue['workflowId'] == 'main' and issue['fieldPath'] == 'operatorId'
    assert service.drafts.read(state['draftId']) == before
    assert service.drafts.validate(state['draftId'])['status'] == 'INCOMPLETE'
    saved = service.drafts.save(state['draftId'], state['draftRevision'])
    assert not saved['dirty'] and saved['project']['dependencies'] == payload['dependencies']
