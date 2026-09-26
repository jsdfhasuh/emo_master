from copy import deepcopy

import pytest
from pydantic import ValidationError

from emo_master.core.presentation.catalog import buildOutputCatalog
from emo_master.core.presentation.models import Presentation
from emo_master.core.presentation.validation import validateBindings
from emo_master.core.project.migration import migrateProjectPayload
from emo_master.core.project.models import ProjectDocument


def testRealManifestsDiscoverInstancesWithoutRunning(project, manifests):
    entries = buildOutputCatalog(project, manifests)
    assert {e.nodeId for e in entries if e.port == "count"} == {None, "a", "b", "c"}
    assert not any(e.operatorId == "vision.source.image_loader" for e in entries)
    overlay = next(e for e in entries if e.port == "overlay")
    assert overlay.spec["required"] is False and "drawOverlay" in overlay.hint
    assert project.workflows["main"].nodes[2].params == {}
    assert validateBindings(project, manifests, publish=True) == []


def testUnboundDraftAndPublishAreDifferent(project, manifests):
    project.presentation.pages["overview"].components[0].bindings = {}
    project.presentation.pages["overview"].resultScopeIds = ["root"]
    assert validateBindings(project, manifests) == []
    assert any(i.code == "unbound" for i in validateBindings(project, manifests, publish=True))


@pytest.mark.parametrize("change", [
    {"nodeId": "gone"}, {"port": "isOK"}, {"expectedType": "boolean"},
    {"resultScopeId": "missing"}, {"fieldPath": ["0"]},
    {"workflowId": "child", "nodeId": "c"},
])
def testBadNonemptyBindingBlocksDebugAndPublish(project, manifests, change):
    source = project.presentation.dataSources["count"]
    project.presentation.dataSources["count"] = type(source).model_validate(
        {**source.model_dump(), **change})
    for publish in [False, True]:
        issues = validateBindings(project, manifests, publish=publish)
        assert any(i.path == "presentation.dataSources.count" for i in issues)


def testPluginConflictNeverWidensToAny(project, manifests):
    project.workflows["main"].nodes[0].outputPorts["count"] = "any"
    assert any(e.issues for e in buildOutputCatalog(project, manifests) if e.nodeId == "a")
    assert any("disagree" in i.message for i in validateBindings(project, manifests))


def testCollectionProjectionStaysCollection(project, manifests):
    source = project.presentation.dataSources["count"]
    source.nodeId, source.port, source.expectedType = "blob", "blobs", "collection"
    source.fieldPath = ["items", "*", "area"]
    card = project.presentation.pages["overview"].components[0]
    card.type, card.bindings = "table", {"rows": "count"}
    assert validateBindings(project, manifests) == []
    source.fieldPath = ["items", "0", "area"]
    assert validateBindings(project, manifests)


@pytest.mark.parametrize("relation,node", [("subflow", "callA"), ("subflow", "callB"), ("loop_body", "loop")])
def testExactCallLocationsAndIterationScope(project, manifests, relation, node):
    from emo_master.core.presentation.models import CallStep
    source = project.presentation.dataSources["count"]
    source.workflowId, source.nodeId = "child", "c"
    source.callPath = [CallStep(nodeId=node, relation=relation)]
    if relation == "loop_body":
        assert any("iteration scope" in i.message for i in validateBindings(project, manifests))
        scope = project.presentation.resultScopes["root"]
        scope.callPath, scope.scopeWorkflowId = deepcopy(source.callPath), "child"
    assert validateBindings(project, manifests) == []


def testWorkflowOutputAndBooleanNotNumeric(project, manifests):
    source = project.presentation.dataSources["count"]
    source.kind, source.nodeId = "workflow_output", None
    assert validateBindings(project, manifests) == []
    project.workflows["main"].outputs["count"] = "bool"
    assert any("output is boolean" in i.message for i in validateBindings(project, manifests))


def testDetailScopeMustBeSupportedOnBothPages(project, manifests):
    project.presentation.pages["detail"].resultScopeIds = []
    assert any("target page" in i.message for i in validateBindings(project, manifests))


@pytest.mark.parametrize("mutation", ["unknown", "duplicate", "overlap", "columns", "enum", "cycle"])
def testInvalidStructuresRejected(project, mutation):
    raw = project.presentation.model_dump()
    children = raw["pages"]["overview"]["components"]
    if mutation == "unknown":
        raw["taskId"] = "must-not-persist"
    elif mutation == "duplicate":
        children[1]["componentId"] = children[0]["componentId"]
    elif mutation == "overlap":
        children[1]["layout"]["row"] = 0
    elif mutation == "columns":
        raw["pages"]["overview"]["layout"]["columns"] = True
    elif mutation == "enum":
        children[0]["type"] = "python_script"
    else:
        children[0]["type"] = "container"
        children[0]["children"] = [children[0]]
    with pytest.raises(ValidationError):
        Presentation.model_validate(raw)


def testExplicitVersionMigrationIsStrictIdempotentAndPreservesLoops(project):
    raw = project.model_dump()
    raw["schemaVersion"] = "2.0"
    raw.pop("presentation")
    raw.pop("resources")
    before = deepcopy(raw)
    ordinary = migrateProjectPayload(raw)
    assert ordinary["schemaVersion"] == "2.1" and "presentation" not in ordinary
    migrated = migrateProjectPayload(raw, enablePresentation=True)
    assert raw == before and migrated == migrateProjectPayload(migrated, enablePresentation=True)
    assert migrated["presentation"]["pages"] == {}
    assert next(n for n in migrated["workflows"]["main"]["nodes"] if n["nodeId"] == "loop")["loop"]["contractVersion"] == 1
    bad = {**before, "presentation": {}}
    with pytest.raises(ValueError):
        migrateProjectPayload(bad, enablePresentation=True)
    bad = {**before, "ignoredField": 1}
    with pytest.raises(ValueError):
        migrateProjectPayload(bad, enablePresentation=True)


def testLegacyV1AndJsonRoundtrip():
    raw = {"version": "1.0", "meta": {"projectId": "old", "name": "Old"},
           "designer": {"nodes": [], "edges": []}}
    migrated = migrateProjectPayload(raw, enablePresentation=True)
    parsed = ProjectDocument.model_validate(migrated)
    assert ProjectDocument.model_validate_json(parsed.model_dump_json()) == parsed
    assert parsed.schemaVersion == "2.2" and parsed.presentation.pages == {}


def testReadOnlyCounterAndRuntimeStatusRequireExplicitNames(project, manifests):
    from emo_master.core.presentation.models import DataSource
    project.presentation.dataSources["count"] = DataSource(
        kind="global_counter", name="production", resultScopeId="root", expectedType="integer")
    assert validateBindings(project, manifests)
    assert validateBindings(project, manifests, counterNames=frozenset({"production"})) == []
    project.presentation.dataSources["count"] = DataSource(
        kind="runtime_status", name="job_state", resultScopeId="root", expectedType="string")
    project.presentation.pages["overview"].components[0].type = "runtime_status"
    assert validateBindings(project, manifests) == []


def testLoopV2SurvivesExplicitProjectUpgrade(project):
    from emo_master.core.workflow.validation import validateProjectDocument
    raw = project.model_dump()
    raw.pop("presentation")
    raw.pop("resources")
    raw["schemaVersion"] = "2.1"
    loop = next(node for node in raw["workflows"]["main"]["nodes"] if node["nodeId"] == "loop")
    loop["loop"]["contractVersion"] = 2
    upgraded = migrateProjectPayload(raw, enablePresentation=True)
    assert next(node for node in upgraded["workflows"]["main"]["nodes"] if node["nodeId"] == "loop")["loop"] == loop["loop"]
    assert validateProjectDocument(ProjectDocument.model_validate(raw)) == validateProjectDocument(
        ProjectDocument.model_validate(upgraded))
