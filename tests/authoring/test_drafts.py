"""Public semantic service coverage; declarations are real source fixtures."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from emo_master.authoring.drafts import DraftService
from emo_master.authoring.errors import AuthoringError
from emo_master.core.project.models import ProjectDocument

ROOT = Path(__file__).resolve().parents[2]


class SourceCatalog:
    """Explicit source fixture, not a live Runtime integration claim."""
    def get_operator(self, target_id, operator_id):
        assert target_id == "source-fixture"
        dirname = {"vision.preprocess.roi": "roi", "vision.preprocess.threshold": "threshold",
                   "vision.io.image_loader": "image_loader"}.get(operator_id)
        if dirname:
            return json.loads((ROOT / "src/emo_master/plugins/builtins" / dirname / "manifest.json").read_text(encoding="utf-8"))
        if operator_id == "fixture.camera":
            return {"operatorId": operator_id, "version": "1.0.0", "displayName": "Fixture camera", "inputPorts": {},
                    "outputPorts": {"image": "image"}, "paramSchema": {"type": "object", "properties": {}},
                    "deviceRequirements": {"camera": {"deviceTypeId": "device.example.simulated_camera"}}}
        raise KeyError(operator_id)

    def get_device_type(self, target_id, type_id):
        assert target_id == "source-fixture"
        value = json.loads((ROOT / "examples/device_plugin/device_types/simulated_camera.json").read_text())
        assert value["deviceTypeId"] == type_id
        return {"declaration": value}


@pytest.fixture
def drafts(tmp_path):
    return DraftService(tmp_path, SourceCatalog())


def apply(service, state, command_type, **fields):
    return service.edit(state["draftId"], state["draftRevision"], {"type": command_type, **fields})


def node(state, node_id):
    return next(item for item in state["project"]["workflows"]["main"]["nodes"] if item["nodeId"] == node_id)


def test_complete_blank_independent_saved_restart_and_detached_reads(drafts, tmp_path):
    first, other = drafts.create("First"), drafts.create("Other")
    project = first["project"]
    assert project["schemaVersion"] == "2.9" and project["presentation"]["schemaVersion"] == "1.2"
    assert project["devices"]["definitions"] == {} and "communication" not in project
    assert {"resources", "production", "globalVariables", "runtime", "dependencies"} <= set(project)
    before = deepcopy(project["project"])
    assert drafts.read(first["draftId"])["project"]["project"] == before
    first = apply(drafts, first, "project.rename", name="Changed")
    assert drafts.read(other["draftId"])["project"]["project"]["name"] == "Other"
    first = drafts.save(first["draftId"], first["draftRevision"])
    saved_metadata = deepcopy(first["project"]["project"])
    first = apply(drafts, first, "project.rename", name="Unsaved")
    restarted = DraftService(tmp_path).read(first["draftId"])
    assert restarted["project"]["project"]["name"] == "Changed"
    assert restarted["draftRevision"] != first["draftRevision"]
    restarted["project"]["runtime"]["eventRetentionPerJob"] = 1
    assert drafts.read(first["draftId"])["project"]["runtime"]["eventRetentionPerJob"] != 1
    first = drafts.undo(first["draftId"], first["draftRevision"])
    assert first["project"]["project"] == saved_metadata
    assert drafts.validate(first["draftId"])["scope"] == "local"


def test_atomic_nested_roi_replace_undo_and_failed_revision(drafts):
    state = drafts.create()
    state = apply(drafts, state, "node.create", workflowId="main", targetId="source-fixture", operatorId="vision.preprocess.roi", nodeId="roi")
    params = {"roiType": "regions", "regions": [
        {"name": "keep", "purpose": "keep", "enabled": True, "roiType": "polygon",
         "points": [{"x": 1., "y": 2.}, {"x": 9., "y": 2.}, {"x": 9., "y": 9.}]},
        {"name": "exclude", "purpose": "exclude", "enabled": False, "roiType": "bbox", "x": 2., "y": 3., "width": 2., "height": 3.}]}
    before = deepcopy(state)
    state = apply(drafts, state, "node.configure", workflowId="main", nodeId="roi", params=params)
    assert node(state, "roi")["params"] == params
    params["regions"][0]["points"][0]["x"] = 800.
    assert node(drafts.read(state["draftId"]), "roi")["params"]["regions"][0]["points"][0]["x"] == 1.
    changed = deepcopy(state)
    state = drafts.undo(state["draftId"], state["draftRevision"])
    assert state["project"] == before["project"]
    state = drafts.redo(state["draftId"], state["draftRevision"])
    assert state["project"] == changed["project"]
    stable = deepcopy(state)
    with pytest.raises(AuthoringError) as error:
        apply(drafts, state, "node.configure", workflowId="main", nodeId="roi", params={"roiType": "invalid"})
    assert error.value.details["diagnostics"][0]["nodeId"] == "roi"
    assert drafts.read(state["draftId"])["project"] == stable["project"]
    assert drafts.read(state["draftId"])["draftRevision"] == stable["draftRevision"]
    with pytest.raises(AuthoringError) as error:
        apply(drafts, before, "project.rename", name="stale")
    assert error.value.code == "DRAFT_REVISION_CONFLICT"
    state = apply(drafts, state, "node.configure", workflowId="main", nodeId="roi", params={"roiType": "bbox"})
    assert "regions" not in node(state, "roi")["params"]


def test_variable_binding_omission_preserves_and_explicit_empty_clears(drafts):
    state = drafts.create()
    state = apply(drafts, state, "variables.configure", definitions={"limit": {"name": "Limit", "type": "integer", "initialValue": 127}})
    state = apply(drafts, state, "node.create", workflowId="main", targetId="source-fixture", operatorId="vision.preprocess.threshold", nodeId="threshold")
    bindings = [{"parameterPath": ["threshold"], "variableId": "limit"}]
    state = apply(drafts, state, "node.configure", workflowId="main", nodeId="threshold", params={"mode": "fixed"}, globalVariableBindings=bindings)
    state = apply(drafts, state, "node.configure", workflowId="main", nodeId="threshold", params={"mode": "otsu"})
    assert node(state, "threshold")["globalVariableBindings"] == bindings
    state = apply(drafts, state, "node.configure", workflowId="main", nodeId="threshold", params={"mode": "fixed"}, globalVariableBindings=[])
    assert not node(state, "threshold").get("globalVariableBindings")


def test_graph_commands_one_undo_and_draft_local_outputs(drafts):
    state = drafts.create()
    for op, node_id in [("vision.io.image_loader", "image"), ("vision.preprocess.threshold", "threshold")]:
        state = apply(drafts, state, "node.create", workflowId="main", targetId="source-fixture", operatorId=op, nodeId=node_id)
    before = deepcopy(state["project"])
    state = apply(drafts, state, "edge.connect", workflowId="main", fromNode="image", fromPort="image", toNode="threshold", toPort="image")
    assert len(state["project"]["workflows"]["main"]["edges"]) == 1
    state = drafts.undo(state["draftId"], state["draftRevision"])
    assert state["project"] == before
    assert any(item["nodeId"] == "threshold" and item["port"] == "mask" for item in drafts.output_choices(state["draftId"])["outputs"])
    state = apply(drafts, state, "node.copy", workflowId="main", nodeId="threshold")
    copied = state["createdIds"]["nodeId"]
    state = apply(drafts, state, "node.move", workflowId="main", nodeId=copied, position={"x": 30., "y": 40.})
    state = apply(drafts, state, "node.delete", workflowId="main", nodeId=copied)
    assert all(n["nodeId"] != copied for n in state["project"]["workflows"]["main"]["nodes"])


def test_complex_controls_devices_variables_pages_production_save_reload(drafts, tmp_path):
    state = drafts.create()
    state = apply(drafts, state, "workflow.create", name="Body", workflowId="body")
    state = apply(drafts, state, "workflow.interface", workflowId="body", inputs={"value": "number"}, outputs={"value": "number"})
    state = apply(drafts, state, "control.create", workflowId="main", kind="subflow", nodeId="call", config={"targetWorkflowId": "body"})
    assert node(state, "call")["inputPorts"] == {"value": "number"}
    state = apply(drafts, state, "control.create", workflowId="main", kind="loop", nodeId="repeat",
                  config={"mode": "repeat", "repeatCount": 2, "maxIterations": 10, "timeoutMs": 1000, "bodyWorkflowId": "body"})
    assert node(state, "repeat")["loop"]["contractVersion"] == 2
    state = apply(drafts, state, "control.create", workflowId="main", kind="dispatch", nodeId="dispatch", inputPorts={"source": "number"},
                  config={"targets": [{"targetSlotId": "worker", "workflowId": "body", "inputs": {"value": "source"}, "queueCapacity": 4}]})
    state = apply(drafts, state, "control.create", workflowId="main", kind="join", nodeId="join", config={"dispatchNodeId": "dispatch"})
    assert node(state, "join")["outputPorts"] == {"worker": "object"}
    state = apply(drafts, state, "workflow.copy", workflowId="body")
    copied_id = state["createdIds"]["workflowId"]
    assert copied_id != "body"
    state = apply(drafts, state, "workflow.delete", workflowId=copied_id)
    declaration = drafts.catalog.get_device_type("source-fixture", "device.example.simulated_camera")["declaration"]
    state = apply(drafts, state, "device.put", targetId="source-fixture", deviceId="camera", definition={
        "name": "Offline camera", "deviceTypeId": declaration["deviceTypeId"], "driverVersion": declaration["version"],
        "connection": {"source": "offline"}, "defaults": {"exposureMode": "manual", "exposureUs": 8000., "gain": 0., "enabled": False}})
    state = apply(drafts, state, "node.create", workflowId="main", targetId="source-fixture", operatorId="fixture.camera", nodeId="camera-node")
    state = apply(drafts, state, "node.devices", workflowId="main", nodeId="camera-node", targetId="source-fixture", deviceBindings={"camera": {"deviceId": "camera"}})
    state = apply(drafts, state, "variables.configure", definitions={"count": {"name": "Count", "type": "integer", "initialValue": 0}})
    state = apply(drafts, state, "page.create", name="Overview")
    page_id = state["createdIds"]["pageId"]
    state = apply(drafts, state, "component.put", pageId=page_id, component={"componentId": "title", "type": "text", "props": {"text": "Ready"}})
    page = deepcopy(state["project"]["presentation"]["pages"][page_id])
    page["targets"] = ["desktop", "web"]
    state = apply(drafts, state, "page.configure", pageId=page_id, page=page)
    state = apply(drafts, state, "production.configure", settings={"autoStart": False, "mode": "continuous", "cycleIntervalMs": 500, "inputs": {}})
    state = apply(drafts, state, "runtime.configure", settings={"maxConcurrentJobs": 4, "startupWorkflowIds": ["main", "body"]})
    state = drafts.save(state["draftId"], state["draftRevision"])
    reloaded = DraftService(tmp_path).read(state["draftId"])
    assert reloaded["project"] == state["project"]
    ProjectDocument.model_validate(reloaded["project"])


def test_page_read_uses_partial_snapshot_and_history(drafts, monkeypatch):
    state = drafts.create()
    state = apply(drafts, state, "page.create", name="Before")
    page_id = state["result"]["pageId"]
    state = apply(drafts, state, "page.rename", pageId=page_id, name="After")
    state = drafts.undo(state["draftId"], state["draftRevision"])
    session = drafts.get_draft(state["draftId"]).session
    def forbidden(*args, **kwargs):
        raise AssertionError("Page read rebuilt the full project")
    monkeypatch.setattr(session, "document", forbidden)
    monkeypatch.setattr(session.workflows, "toPayload", forbidden)
    first = drafts.read_presentation(state["draftId"])
    assert first["presentation"]["pages"][page_id]["name"] == "Before"
    first["presentation"]["pages"][page_id]["name"] = "Caller mutation"
    assert drafts.read_presentation(state["draftId"])["presentation"]["pages"][page_id]["name"] == "Before"


def test_local_validate_offline_incomplete_declarations_and_saving(drafts, tmp_path):
    state = drafts.create()
    state = apply(drafts, state, "node.create", workflowId="main", targetId="source-fixture", operatorId="vision.io.image_loader", nodeId="image")
    state = drafts.save(state["draftId"], state["draftRevision"])
    offline = DraftService(tmp_path)
    report = offline.validate(state["draftId"])
    assert report["status"] == "INCOMPLETE" and report["scope"] == "local"
    assert report["compileStatus"] == report["executionStatus"] == "not_run"
    offline.save(state["draftId"], offline.read(state["draftId"])["draftRevision"])


def test_noop_does_not_change_revision_or_history(drafts):
    state = drafts.create('Same')
    session = drafts.get_draft(state['draftId']).session
    changed = apply(drafts, state, 'project.rename', name='Same')
    assert not changed['changed'] and changed['draftRevision'] == state['draftRevision']
    assert not session._undo and not session._redo and not changed['dirty']


def test_invalid_nonempty_page_binding_and_navigation_roll_back(drafts):
    state = drafts.create()
    state = apply(drafts, state, 'node.create', workflowId='main', targetId='source-fixture',
                  operatorId='vision.preprocess.threshold', nodeId='threshold')
    state = apply(drafts, state, 'page.create', name='Overview')
    page_id = state['createdIds']['pageId']
    presentation = deepcopy(state['project']['presentation'])
    presentation['resultScopes']['main-result'] = {'entryWorkflowId': 'main', 'scopeWorkflowId': 'main', 'callPath': []}
    presentation['pages'][page_id]['resultScopeIds'] = ['main-result']
    state = apply(drafts, state, 'presentation.configure', presentation=presentation)
    state = apply(drafts, state, 'component.put', pageId=page_id, component={'componentId': 'view', 'type': 'image'})
    before = drafts.read(state['draftId'])
    history = len(drafts.get_draft(state['draftId']).session._undo)
    with pytest.raises(AuthoringError) as caught:
        apply(drafts, state, 'component.bind', pageId=page_id, componentId='view', property='image', source={
            'kind': 'node_output', 'resultScopeId': 'main-result', 'workflowId': 'main', 'nodeId': 'threshold',
            'port': 'does-not-exist', 'expectedType': 'image'})
    assert caught.value.details['diagnostics'][0]['fieldPath'].startswith('presentation.dataSources.')
    assert drafts.read(state['draftId']) == before
    assert len(drafts.get_draft(state['draftId']).session._undo) == history
    with pytest.raises(AuthoringError):
        apply(drafts, state, 'component.put', pageId=page_id, component={'componentId': 'nav', 'type': 'navigation_button',
            'actions': {'clicked': {'type': 'navigate', 'pageId': 'missing-page'}}})
    assert drafts.read(state['draftId']) == before


def test_declarations_are_owned_by_each_draft(drafts):
    class TargetCatalog(SourceCatalog):
        def get_operator(self, target_id, operator_id):
            value = super().get_operator('source-fixture', operator_id)
            value['paramSchema']['description'] = target_id
            return value
    drafts.catalog = TargetCatalog()
    first = drafts.create('First')
    first = apply(drafts, first, 'node.create', workflowId='main', targetId='first-target',
                  operatorId='vision.preprocess.threshold', nodeId='threshold')
    second = drafts.create('Second')
    second = apply(drafts, second, 'node.create', workflowId='main', targetId='second-target',
                   operatorId='vision.preprocess.threshold', nodeId='threshold')
    assert node(first, 'threshold')['paramSchema']['description'] == 'first-target'
    assert node(second, 'threshold')['paramSchema']['description'] == 'second-target'
    first_draft = drafts.get_draft(first['draftId'])
    assert drafts._manifests(first_draft)['vision.preprocess.threshold'].paramSchema['description'] == 'first-target'
    with pytest.raises(AuthoringError) as caught:
        apply(drafts, first, 'node.create', workflowId='main', targetId='second-target',
              operatorId='vision.preprocess.threshold', nodeId='conflict')
    assert caught.value.code == 'DECLARATION_CONFLICT'
    assert drafts.read(first['draftId'])['project'] == first['project']
    assert drafts.read(first['draftId'])['draftRevision'] == first['draftRevision']


def test_invalid_loop_contract_has_location_and_rolls_back(drafts):
    state = drafts.create()
    state = apply(drafts, state, 'workflow.create', name='Body', workflowId='body')
    state = apply(drafts, state, 'workflow.interface', workflowId='body', inputs={}, outputs={'answer': 'number'})
    with pytest.raises(AuthoringError) as caught:
        apply(drafts, state, 'control.create', workflowId='main', kind='loop', nodeId='loop', config={
            'mode': 'repeat', 'repeatCount': 0, 'maxIterations': 10, 'timeoutMs': 1000, 'bodyWorkflowId': 'body'})
    issue = caught.value.details['diagnostics'][0]
    assert issue['code'] == 'E_LOOP_ZERO_OUTPUT_UNSATISFIABLE'
    assert issue['workflowId'] == 'main' and issue['nodeId'] == 'loop' and issue['fieldPath'] == 'loop.repeatCount'
    assert drafts.read(state['draftId'])['project'] == state['project']
    assert drafts.read(state['draftId'])['draftRevision'] == state['draftRevision']


def test_local_validate_reports_missing_input_bytes_and_inputs_without_target(drafts):
    from emo_master.authoring.files import FilesService
    state = drafts.create()
    for operator_id, node_id in [('vision.io.image_loader', 'image'), ('vision.preprocess.threshold', 'threshold')]:
        state = apply(drafts, state, 'node.create', workflowId='main', targetId='source-fixture', operatorId=operator_id, nodeId=node_id)
    drafts.catalog = None
    report = drafts.validate(state['draftId'])
    assert report['status'] == 'INCOMPLETE'
    assert any(issue['nodeId'] == 'image' and issue['fieldPath'] == 'params.imagePath' for issue in report['diagnostics'])
    assert any(issue['nodeId'] == 'threshold' and issue['fieldPath'] == 'inputPorts.image' for issue in report['diagnostics'])
    assert drafts.read(state['draftId'])['draftRevision'] == state['draftRevision']
    content = b'local-validation-byte-fixture'
    uploaded = FilesService(drafts).upload(state['draftId'], state['draftRevision'], 'input.png', 'input_image',
                                         content, content_length=len(content))
    state = drafts.read(state['draftId'])
    state = apply(drafts, state, 'resource.bind', resourceId=uploaded['resourceId'],
                  target={'workflowId': 'main', 'nodeId': 'image', 'parameterPath': ['imagePath']})
    state = apply(drafts, state, 'edge.connect', workflowId='main', fromNode='image', fromPort='image', toNode='threshold', toPort='image')
    report = drafts.validate(state['draftId'], state['draftRevision'])
    assert report['status'] == 'PASSED' and report['diagnostics'] == []
    assert report['compileStatus'] == report['executionStatus'] == 'not_run'


def test_local_variable_reference_and_site_overlap_are_checked(drafts):
    class ScalarCatalog(SourceCatalog):
        def get_operator(self, target_id, operator_id):
            return {'operatorId': operator_id, 'version': 'fixture-1', 'inputPorts': {}, 'outputPorts': {},
                    'paramSchema': {'type': 'object', 'properties': {'address': {
                        'type': 'string', 'default': '', 'xResourcePurpose': 'device_address'}}}, 'deviceRequirements': {}}
    drafts.catalog = ScalarCatalog()
    state = drafts.create()
    state = apply(drafts, state, 'variables.configure', definitions={'host': {'name': 'Host', 'type': 'string', 'initialValue': 'offline'}})
    state = apply(drafts, state, 'node.create', workflowId='main', targetId='source-fixture', operatorId='fixture.scalar', nodeId='address')
    state = apply(drafts, state, 'node.configure', workflowId='main', nodeId='address', params={},
                  globalVariableBindings=[{'parameterPath': ['address'], 'variableId': 'host'}])
    with pytest.raises(AuthoringError):
        apply(drafts, state, 'site.bind', name='host', purpose='device_address',
              target={'workflowId': 'main', 'nodeId': 'address', 'parameterPath': ['address']})
    assert drafts.read(state['draftId'])['project'] == state['project']
    assert drafts.read(state['draftId'])['draftRevision'] == state['draftRevision']
    payload = deepcopy(state['project'])
    node({'project': payload}, 'address')['globalVariableBindings'][0]['variableId'] = 'missing'
    imported = drafts.create(payload=payload)
    report = drafts.validate(imported['draftId'])
    assert report['status'] == 'FAILED'
    assert any(issue['code'] == 'E_VARIABLE_UNKNOWN' and issue['nodeId'] == 'address' for issue in report['diagnostics'])
