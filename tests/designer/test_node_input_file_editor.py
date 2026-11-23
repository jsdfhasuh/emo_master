from pathlib import Path
import hashlib
from types import SimpleNamespace

import pytest
from PySide2.QtWidgets import QFileDialog

from emo_master.apps.designer.state.project_store import loadProjectDocument
from emo_master.apps.designer.ui.resource_bindings import bindingTargets
from emo_master.core.contracts.port_types import normalizePortType
from emo_master.plugins.builtins._coordinate_operators import CoordinateReaderOperator
from emo_master.plugins.builtins.image_loader.operator import ImageLoaderOperator
from emo_master.plugins.builtins.video_loader.operator import VideoLoaderOperator
from emo_master.plugins.builtins.yolo_classification.operator import YoloClassificationOperator
from emo_master.plugins.builtins.yolo_inference.operator import YoloInferenceOperator


@pytest.mark.parametrize(("meta", "field", "suffix", "firstSave"), [
    (ImageLoaderOperator.meta, "imagePath", ".png", False),
    (ImageLoaderOperator.meta, "imagePath", ".png", True),
    (VideoLoaderOperator.meta, "videoPath", ".mp4", False),
    (CoordinateReaderOperator.meta, "filePath", ".csv", False),
    (YoloInferenceOperator.meta, "modelPath", ".onnx", False),
    (YoloClassificationOperator.meta, "modelPath", ".onnx", False),
], ids=["image", "first-save", "video", "coordinates", "detections", "classification"])
def testNodePickerImportsExternalFileAndKeepsSavedDraftPortable(
    ownedDesignerWindow, tmp_path, monkeypatch, meta, field, suffix, firstSave
):
    window = ownedDesignerWindow(SimpleNamespace(listOperators=lambda: [meta]))
    window.operatorCatalogController.applyOperators([meta], window._classifyOperator)
    directory = tmp_path / "工程"
    directory.mkdir()
    project = directory / "model.emoproj"
    if not firstSave:
        assert window.saveProjectToDirectory(str(project))
    inputs = {key: normalizePortType(spec) for key, spec in meta.inputPorts.items()}
    outputs = {key: normalizePortType(spec) for key, spec in meta.outputPorts.items()}
    nodeId = window.flowModel.addNode(meta.operatorId, meta.displayName, inputs, outputs, paramSchema=meta.paramSchema)
    window._addControlNodeToScene(nodeId, sceneX=0, sceneY=0)
    window.openNodeParamDialog(nodeId)
    editor = window.nodeParamDialog
    picker = editor._schemaForm._controls[field]
    external = tmp_path / ("选择的输入文件" + suffix)
    external.write_bytes(b"selected test file")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_: (str(external), ""))
    picker._browseButton.click()
    assert editor.applyChanges(), editor._statusLabel.text()
    assert window.saveProjectToDirectory(str(project))
    relative = picker.text()
    assert not Path(relative).is_absolute() and Path(relative).name == external.name
    assert (directory / relative).read_bytes() == external.read_bytes()
    assert window.flowModel.nodes[nodeId].params[field] == relative and not editor.isDirty()
    saved = loadProjectDocument(project)
    assert next(n for n in saved.workflows["main"].nodes if n.nodeId == nodeId).params[field] == relative
    assert {item.path for item in saved.resources.items.values()} == {relative}
    assert not saved.resources.parameterBindings
    assert not bindingTargets(saved, {}, {meta.operatorId: {"paramSchema": meta.paramSchema}})

    inside = directory / ("工程内文件" + suffix)
    inside.write_bytes(b"other file")
    otherNode = window.flowModel.addNode(meta.operatorId, "其他节点", inputs, outputs, paramSchema=meta.paramSchema)
    window.flowModel.nodes[otherNode].params[field] = "其他节点的文件" + suffix
    window._addControlNodeToScene(otherNode, sceneX=300, sceneY=0)
    # A legacy binding from before the node selected its own file must disappear on Apply.
    plan = window.workflowStore.projectExtensions["resources"]
    oldId = next(iter(plan["items"]))
    oldBinding = dict(resourceId=oldId, target=dict(workflowId="main", nodeId=nodeId, parameterPath=[field]))
    otherBinding = dict(resourceId=oldId, target=dict(workflowId="main", nodeId=otherNode, parameterPath=[field]))
    plan["parameterBindings"] = [oldBinding, otherBinding]
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_: (str(inside), ""))
    picker._browseButton.click()
    assert picker.text() == inside.name
    assert editor.applyChanges(), editor._statusLabel.text()
    assert window.flowModel.nodes[nodeId].params[field] == inside.name
    assert window.flowModel.nodes[otherNode].params[field] == "其他节点的文件" + suffix
    current = window.workflowStore.projectExtensions["resources"]
    assert current["parameterBindings"] == [otherBinding]
    assert any(item["path"] == inside.name and item["sha256"] == hashlib.sha256(inside.read_bytes()).hexdigest()
               for item in current["items"].values())


def testPageTestImageUpdatesNodeAndSupportsUndo(tmp_path):
    from emo_master.apps.designer.page_designer.resources import registerImage
    from emo_master.apps.designer.state.project_edit_session import ProjectEditSession
    from emo_master.apps.designer.state.workflow_store import WorkflowStore

    store = WorkflowStore()
    store.get("main").nodes.append(dict(nodeId="input", operatorId=ImageLoaderOperator.meta.operatorId,
                                        kind="operator", params={"imagePath": "before.png"}))
    session = ProjectEditSession(store.toPayload())
    after = tmp_path / "new.png"
    after.write_bytes(b"new")
    registerImage(session, tmp_path, "main", "input", after)
    document = session.document()
    assert next(n for n in document.workflows["main"].nodes if n.nodeId == "input").params["imagePath"] == after.name
    assert {item.path for item in document.resources.items.values()} == {after.name}
    assert not document.resources.parameterBindings
    session.undo()
    node = next(n for n in session.document().workflows["main"].nodes if n.nodeId == "input")
    assert node.params["imagePath"] == "before.png" and not session.document().resources.items
