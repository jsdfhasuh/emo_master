import hashlib

import pytest

from emo_master.apps.designer.state.workflow_store import WorkflowStore
from emo_master.core.project.models import ProjectDocument, WorkflowEdge, WorkflowNode
from emo_master.core.project.node_input_files import (
    NODE_INPUT_FILES, alignNodeInputFiles, nodeInputResource, projectInputFilePath,
)
from emo_master.core.project.resources import ParameterTarget, Resource, ResourceBinding
from emo_master.core.workflow.errors import WorkflowCompileError


@pytest.mark.parametrize("operator,field", NODE_INPUT_FILES.items())
def testLiteralSelectsTransferredBytesAndPreservesSharedOldInput(tmp_path, operator, field):
    document = ProjectDocument.model_validate(WorkflowStore().toPayload())
    workflow = document.workflows[document.entryWorkflowId]
    for name in ("old.dat", "new.dat"):
        (tmp_path / name).write_bytes(name.encode())
    nodes = [WorkflowNode(nodeId=nid, operatorId=operator, params={field: path})
             for nid, path in (("changed", "new.dat"), ("other", "old.dat"), ("unfinished", "missing"))]
    workflow.nodes.extend(nodes)
    plan = document.resources
    plan.items["shared"] = Resource(path="old.dat", purpose="model",
                                     sha256=hashlib.sha256(b"old.dat").hexdigest(), size=7)
    for node in nodes[:2]:
        target = ParameterTarget(workflowId=document.entryWorkflowId, nodeId=node.nodeId, parameterPath=[field])
        plan.parameterBindings.append(ResourceBinding(target=target, resourceId="shared"))
    only = {(document.entryWorkflowId, "changed")}
    updated = alignNodeInputFiles(document, tmp_path, only=only)
    assert [b.target.nodeId for b in updated.resources.parameterBindings] == ["other"]
    assert updated.resources.items["shared"] == plan.items["shared"]
    transferred = nodeInputResource(updated, workflow, nodes[0])[1]
    assert transferred.path == "new.dat" and transferred.sha256 == hashlib.sha256(b"new.dat").hexdigest()
    assert plan.items.keys() == {"shared"}
    assert alignNodeInputFiles(updated, tmp_path, only=only) == updated
    with pytest.raises(WorkflowCompileError) as failure:
        alignNodeInputFiles(document, tmp_path)
    issue = failure.value.issues[0]
    assert (issue.workflowId, issue.nodeId, issue.fieldPath) == (document.entryWorkflowId, "unfinished", "params." + field)
    assert alignNodeInputFiles(document, tmp_path, bestEffort=True).resources.items == updated.resources.items


def testConnectedVideoSkipsUnusedFileAndDropsItsOldBinding(tmp_path):
    document = ProjectDocument.model_validate(WorkflowStore().toPayload())
    workflow = document.workflows[document.entryWorkflowId]
    workflow.nodes.append(WorkflowNode(nodeId="video", operatorId="vision.io.video_loader", params={"videoPath": "gone.avi"}))
    workflow.edges.append(WorkflowEdge(fromNode="upstream", fromPort="file", toNode="video", toPort="videoPath"))
    document.resources.items["old"] = Resource(path="old.avi", purpose="input_file", sha256="a"*64, size=0)
    target = ParameterTarget(workflowId=document.entryWorkflowId, nodeId="video", parameterPath=["videoPath"])
    document.resources.parameterBindings.append(ResourceBinding(target=target, resourceId="old"))
    updated = alignNodeInputFiles(document, tmp_path)
    assert updated.resources.parameterBindings == [] and updated.resources.items == {}


def testSingleNodeReapplicationReclaimsUnreferencedDerivedDescriptor(tmp_path):
    document = ProjectDocument.model_validate(WorkflowStore().toPayload())
    wf = document.entryWorkflowId
    node = WorkflowNode(nodeId="detect", operatorId="vision.inference.yolo", params={"modelPath": "first.onnx"})
    document.workflows[wf].nodes.append(node)
    for name in ("first.onnx", "new.onnx"):
        (tmp_path / name).write_bytes(name.encode())
    updated = alignNodeInputFiles(document, tmp_path)
    updated.workflows[wf].nodes[-1].params["modelPath"] = "new.onnx"
    replaced = alignNodeInputFiles(updated, tmp_path, only={(wf, "detect")})
    assert [item.path for item in replaced.resources.items.values()] == ["new.onnx"]


def testExplicitImportKeepsFileNameAndNeverOverwritesPreviousSelection(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    external = tmp_path / "model.onnx"
    external.write_bytes(b"old")
    old = projectInputFilePath(external, project, "vision.inference.yolo")
    assert projectInputFilePath(external, project, "vision.inference.yolo") == old
    external.write_bytes(b"new")
    new = projectInputFilePath(external, project, "vision.inference.yolo")
    assert new != old and (project / new).name == "model.onnx"
    assert (project / old).read_bytes() == b"old" and (project / new).read_bytes() == b"new"
    assert projectInputFilePath(project / new, project, "vision.inference.yolo") == new
