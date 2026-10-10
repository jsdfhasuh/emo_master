from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from emo_master.apps.designer.ui.main_window import MainWindow
from emo_master.plugins.builtins.image_batch_loader.operator import ImageBatchLoaderOperator
from tests.designer.test_main_window_project_save_load import RuntimeClientStub


EXAMPLE = Path(__file__).resolve().parents[2] / "examples/image_batch_while_portable/image-batch-while.emoproj"


def _window(ownedDesignerWindow, monkeypatch, client):
    # Control catalog arrival without a background worker racing the assertions.
    monkeypatch.setattr(MainWindow, "refreshOperators", lambda self, **kwargs: None)
    return ownedDesignerWindow(client, settingsStore=SimpleNamespace(
        value=lambda key, default=None: default, setValue=lambda key, value: None,
    ))


def _catalog(window, operators):
    window.operatorCatalog = window.operatorCatalogController.applyOperators(
        operators, window._classifyOperator,
    )


@pytest.mark.parametrize("embedded", [True, False])
def testMinimalProjectHydratesMetadataAndEditorRoundTrips(
    tmp_path, monkeypatch, ownedDesignerWindow, embedded,
):
    from PySide2.QtWidgets import QLineEdit

    from emo_master.apps.designer.services.runtime_client import RuntimeClient
    from emo_master.apps.runtime.grpc_server.service import RuntimeService

    service = RuntimeService(dbPath=tmp_path / "runtime.db", workspaceRoot=tmp_path / "jobs")
    client = RuntimeClient(service)
    try:
        window = _window(ownedDesignerWindow, monkeypatch, client if embedded else RuntimeClientStub())
        operators = client.listOperators()
        if not embedded:
            _catalog(window, operators)
        original = EXAMPLE.read_bytes()
        assert window.loadProjectDirectory(str(EXAMPLE))
        assert not window.pageCoordinator.session.dirty
        window.activateWorkflow("body")
        node = window.flowModel.nodes["load"]
        assert node.paramSchema == ImageBatchLoaderOperator.meta.paramSchema
        assert node.outputPorts["image"] == "image"
        assert node.outputPorts["hasNext"] == "boolean"
        assert node.inputPorts == {"reset": "boolean"}
        assert node.displayName == ImageBatchLoaderOperator.meta.displayName
        assert len(window.flowModel.edges) == 3
        assert window.flowModel.nodes["blur"].paramSchema["properties"]["kernelSize"]
        _catalog(window, operators)
        before = deepcopy(window.flowModel.toProjectGraph())
        dirtyBefore = window.pageCoordinator.session.dirty
        window.openNodeParamDialog("load")
        editor = window.nodeParamDialog
        form = editor._schemaForm
        assert set(form._controls) == {"folderPath", "colorMode", "recursive"}
        assert editor.collectParams() == {"folderPath": "images", "colorMode": "color", "recursive": False}
        assert not editor.isDirty()
        assert window.pageCoordinator.session.dirty == dirtyBefore
        assert window.flowModel.toProjectGraph() == before
        folder = tmp_path / "pictures"
        folder.mkdir()
        form._controls["folderPath"].findChild(QLineEdit).setText(str(folder))
        assert editor.applyChanges()
        destination = tmp_path / "saved.emoproj"
        assert window.saveProjectToDirectory(str(destination))
        assert window.loadProjectDirectory(str(destination))
        window.activateWorkflow("body")
        restored = window.flowModel.nodes["load"]
        assert restored.params["folderPath"] == str(folder)
        assert restored.paramSchema == node.paramSchema
        assert restored.outputPorts == node.outputPorts
        assert len(window.flowModel.edges) == 3
        assert EXAMPLE.read_bytes() == original
    finally:
        client.close()
        service.close()


def testEditorUsesLateCatalogWithoutMutatingSavedGraph(monkeypatch, ownedDesignerWindow):
    window = _window(ownedDesignerWindow, monkeypatch, RuntimeClientStub())
    assert window.loadProjectDirectory(str(EXAMPLE))
    assert not window.pageCoordinator.session.dirty
    window.activateWorkflow("body")
    node = window.flowModel.nodes["load"]
    assert node.paramSchema == {}
    meta = ImageBatchLoaderOperator.meta
    schema = deepcopy(meta.paramSchema)
    _catalog(window, [SimpleNamespace(
        operatorId=meta.operatorId, displayName=meta.displayName, paramSchema=schema,
    )])
    before = deepcopy(window.flowModel.toProjectGraph())
    dirtyBefore = window.pageCoordinator.session.dirty
    window.openNodeParamDialog("load")
    editor = window.nodeParamDialog
    assert set(editor._schemaForm._controls) == {"folderPath", "colorMode", "recursive"}
    assert editor.collectParams()["folderPath"] == "images"
    assert not editor.isDirty()
    assert window.pageCoordinator.session.dirty == dirtyBefore
    assert window.flowModel.toProjectGraph() == before
    resolved = window._nodeEditorSchema(node)
    resolved["properties"]["folderPath"]["default"] = "changed"
    assert schema == meta.paramSchema
    assert node.paramSchema == {}


def testNormalizationPreservesSavedContractsAndOwnsFallbackSchema(monkeypatch, ownedDesignerWindow):
    window = _window(ownedDesignerWindow, monkeypatch, RuntimeClientStub())
    manifest = json.loads((EXAMPLE.parents[2] / (
        "src/emo_master/plugins/builtins/image_batch_loader/manifest.json"
    )).read_text(encoding="utf-8"))
    _catalog(window, [SimpleNamespace(**manifest)])
    payload = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    original = deepcopy(payload)
    resolved = window.pageCoordinator.normalizeDraft(payload)
    hydrated = resolved["workflows"]["body"]["nodes"][1]
    hydrated["paramSchema"]["properties"]["folderPath"]["default"] = "local-only"
    assert payload == original
    assert manifest["paramSchema"]["properties"]["folderPath"]["default"] == ""

    node = payload["workflows"]["body"]["nodes"][1]
    node.update({
        "displayName": "Custom loader", "inputPorts": {"saved": "string"},
        "outputPorts": {"saved": "number"},
        "paramSchema": {"type": "object", "properties": {}},
    })
    resolved = window.pageCoordinator.normalizeDraft(payload)
    assert resolved["workflows"]["body"]["nodes"][1] == node
