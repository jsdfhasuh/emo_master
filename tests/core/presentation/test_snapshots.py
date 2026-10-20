from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
import hashlib
import json
from pathlib import Path

import pytest

from emo_master.core.presentation.models import Component
from emo_master.core.project.models import ProjectDocument, WorkflowNode
from emo_master.core.project.resources import ResourcePlan
from emo_master.core.project.snapshots import freezeProjectSnapshot


def freeze(project, manifests, root, **kwargs):
    return freezeProjectSnapshot(project, manifests, mode=kwargs.pop("mode", "debug"),
                                 resourceRoot=root, siteDataRoot=root, **kwargs)


def addImageResource(project, root):
    content = b"immutable local input fixture"
    (root / "input.bin").write_bytes(content)
    project.workflows["main"].nodes.append(WorkflowNode(
        nodeId="loader", operatorId="vision.io.image_loader", params={"imagePath": "Z:/developer/input.png"}))
    project.resources = ResourcePlan.model_validate({
        "items": {"input": {"path": "input.bin", "sha256": hashlib.sha256(content).hexdigest(),
                            "size": len(content), "purpose": "input_image"}},
        "parameterBindings": [{"target": {"workflowId": "main", "nodeId": "loader",
                                           "parameterPath": ["imagePath"]}, "resourceId": "input"}]})


def testSnapshotsAreDetachedAndLayoutDoesNotChangeExecutionOrCapture(project, manifests, tmp_path):
    before = project.model_dump()
    first = freeze(project, manifests, tmp_path)
    project.presentation.pages["overview"].name = "new title"
    project.presentation.pages["overview"].components[0].props.unit = "件"
    project.workflows["main"].nodes[0].displayName = "Renamed node"
    second = freeze(project, manifests, tmp_path)
    assert first.executionRevision == second.executionRevision
    assert first.capturePlanRevision == second.capturePlanRevision
    assert json.loads(first.projectJson) == before
    with pytest.raises(FrozenInstanceError):
        first.projectJson = "changed"
    project.workflows["main"].nodes[2].params["minArea"] = 55
    changed = freeze(project, manifests, tmp_path)
    assert changed.executionRevision != first.executionRevision
    project.presentation.dataSources["count"].nodeId = "b"
    rebound = freeze(project, manifests, tmp_path)
    assert rebound.executionRevision == changed.executionRevision
    assert rebound.capturePlanRevision != changed.capturePlanRevision


def testPluginVersionAndNormalizedDefaultsAreFingerprintInputs(project, manifests, tmp_path):
    first = freeze(project, manifests, tmp_path)
    project.workflows["main"].nodes[2].params["drawOverlay"] = False
    assert freeze(project, manifests, tmp_path).executionRevision == first.executionRevision
    manifests["vision.analysis.blob"] = replace(manifests["vision.analysis.blob"], version="1.2.1")
    assert freeze(project, manifests, tmp_path).executionRevision != first.executionRevision


def testResourceRelocationKeepsFingerprintAndNeverFallsBack(project, manifests, tmp_path):
    left, right = tmp_path / "left", tmp_path / "right"
    left.mkdir()
    right.mkdir()
    addImageResource(project, left)
    (right / "input.bin").write_bytes((left / "input.bin").read_bytes())
    a, b = freeze(project, manifests, left), freeze(project, manifests, right)
    assert a.executionRevision == b.executionRevision
    assert a.parametersJson != b.parametersJson
    assert json.loads(b.parametersJson)["main"]["loader"]["imagePath"] == str(right / "input.bin")
    (right / "input.bin").unlink()
    with pytest.raises(ValueError, match="missing"):
        freeze(project, manifests, right)
    (right / "input.bin").write_bytes(b"x" * project.resources.items["input"].size)
    with pytest.raises(ValueError, match="digest"):
        freeze(project, manifests, right)


@pytest.mark.parametrize("path", ["../input", "C:/input", "/input", "a\\input", "a/./input", "a//input", ""])
def testResourcePathTraversalRejected(project, path):
    with pytest.raises(ValueError):
        ResourcePlan.model_validate({"items": {"bad": {"path": path, "sha256": "0" * 64,
                                                      "size": 0, "purpose": "model"}}})


def testResourceAndSiteTargetOverlapRejected():
    target = {"workflowId": "main", "nodeId": "n", "parameterPath": ["nested"]}
    with pytest.raises(ValueError, match="overlap"):
        ResourcePlan.model_validate({"parameterBindings": [{"target": target, "resourceId": "r"}],
            "siteBindings": [{"target": {**target, "parameterPath": ["nested", "value"]},
                              "field": "f", "purpose": "device_address"}]})


def testSiteWhitelistCannotOverrideAlgorithmAndMissingFieldsFail(project, manifests, tmp_path):
    raw = project.model_dump()
    raw["resources"]["siteBindings"] = [{"target": {"workflowId": "main", "nodeId": "blob",
                                                   "parameterPath": ["minArea"]},
                                         "field": "threshold", "purpose": "device_address"}]
    project = ProjectDocument.model_validate(raw)
    with pytest.raises(ValueError, match="whitelist"):
        freeze(project, manifests, tmp_path, siteValues={"threshold": "55"})
    with pytest.raises(ValueError, match="undeclared"):
        freeze(project, manifests, tmp_path, siteValues={"arbitrary": "55"})


def testDebugReleaseStatePathsAndOutputFilesAreIsolated(project, manifests, tmp_path):
    # Trusted metadata only: exercise declared output-file adapter with the real saver manifest.
    from dataclasses import fields
    from emo_master.core.plugin.models import PluginManifest
    root = Path(__file__).resolve().parents[3] / "src/emo_master/plugins/builtins"
    raw = json.loads((root / "image_saver/manifest.json").read_text(encoding="utf-8"))
    manifests[raw["operatorId"]] = PluginManifest(**{f.name: raw[f.name] for f in fields(PluginManifest)
                                                    if f.name in raw})
    project.workflows["main"].nodes.append(WorkflowNode(nodeId="save", operatorId=raw["operatorId"]))
    project.resources = ResourcePlan.model_validate({"siteBindings": [{
        "target": {"workflowId": "main", "nodeId": "save", "parameterPath": ["outputPath"]},
        "field": "output", "purpose": "output_file"}]})
    values = {"output": "result.png"}
    release = freeze(project, manifests, tmp_path, mode="release", releaseRevision="approved-1", siteValues=values)
    debug = freeze(project, manifests, tmp_path, siteValues=values)
    another = freeze(project, manifests, tmp_path, siteValues=values)
    assert len({release.runtimeDbPath, debug.runtimeDbPath, another.runtimeDbPath}) == 3
    assert len({release.outputRoot, debug.outputRoot, another.outputRoot}) == 3
    for snapshot in [release, debug, another]:
        output = json.loads(snapshot.parametersJson)["main"]["save"]["outputPath"]
        assert Path(output).parent == Path(snapshot.outputRoot)
    assert release.executionRevision == debug.executionRevision
    changed = freeze(project, manifests, tmp_path, siteValues={"output": "another.png"})
    assert changed.siteBindingRevision != debug.siteBindingRevision
    assert changed.executionRevision == debug.executionRevision
    assert not list(tmp_path.iterdir())  # model preparation creates no database/output directories
    with pytest.raises(ValueError, match="required site"):
        freeze(project, manifests, tmp_path)
    with pytest.raises(ValueError, match="relative"):
        freeze(project, manifests, tmp_path, siteValues={"output": "../release.png"})


def testUnboundDebugIsAllowedButBadBindingsAndReleaseAreRejected(project, manifests, tmp_path):
    project.presentation.pages["detail"].components.append(Component(componentId="empty", type="number"))
    freeze(project, manifests, tmp_path)
    with pytest.raises(ValueError, match="unbound"):
        freeze(project, manifests, tmp_path, mode="release", releaseRevision="r1")
    project.presentation.dataSources["count"].nodeId = "deleted"
    with pytest.raises(ValueError, match="dataSources.count"):
        freeze(project, manifests, tmp_path)


def testManifestFinalParameterValidationRejectsBoolAsInteger(project, manifests, tmp_path):
    project.workflows["main"].nodes[2].params["minArea"] = True
    with pytest.raises(ValueError, match="expected integer"):
        freeze(project, manifests, tmp_path)


def testResourceInputIsNotMutated(project, manifests, tmp_path):
    addImageResource(project, tmp_path)
    before = deepcopy(project.model_dump())
    freeze(project, manifests, tmp_path)
    assert project.model_dump() == before


def testIllegalWorkflowAndUndeclaredFileCannotPrepare(project, manifests, tmp_path):
    from emo_master.core.project.models import WorkflowEdge
    project.workflows["main"].edges.append(WorkflowEdge(
        fromNode="deleted", fromPort="count", toNode="output", toPort="count"))
    with pytest.raises(ValueError, match="unknown node"):
        freeze(project, manifests, tmp_path)
    project.workflows["main"].edges.clear()
    project.workflows["main"].nodes.append(WorkflowNode(
        nodeId="loader", operatorId="vision.io.image_loader", params={"imagePath": "Z:/dev.png"}))
    with pytest.raises(ValueError, match="explicit resource"):
        freeze(project, manifests, tmp_path)
