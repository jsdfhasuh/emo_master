from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil

import cv2
import numpy as np

from emo_master.apps.runtime.grpc_server.generated import runtime_pb2
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from tests.runtime.runtime_test_utils import waitForTerminal


def _writePreviewProject(projectDir: Path, imagePath: Path) -> None:
    payload = {
        "version": "1.0",
        "meta": {"name": "preview"},
        "runtime": {"sourceImagePath": ""},
        "designer": {
            "nodes": [
                {
                    "nodeId": "loader",
                    "operatorId": "vision.io.image_loader",
                    "params": {"imagePath": str(imagePath), "colorMode": "color"},
                },
                {
                    "nodeId": "roi",
                    "operatorId": "vision.preprocess.roi",
                    "params": {
                        "roiType": "bbox",
                        "x": 2.0,
                        "y": 3.0,
                        "width": 8.0,
                        "height": 7.0,
                    },
                },
            ],
            "edges": [
                {
                    "fromNode": "loader",
                    "fromPort": "image",
                    "toNode": "roi",
                    "toPort": "image",
                }
            ],
        },
    }
    (projectDir / "project.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


def testRuntimePublishesValidatedEditorAssetAndRunsFormalRoiPreview(
    tmp_path: Path,
) -> None:
    projectDir = tmp_path / "project"
    projectDir.mkdir()
    image = np.zeros((20, 24, 3), dtype=np.uint8)
    image[3:10, 2:10] = (20, 120, 240)
    imagePath = projectDir / "source.png"
    assert cv2.imwrite(str(imagePath), image)
    _writePreviewProject(projectDir, imagePath)
    service = RuntimeService(
        dbPath=tmp_path / "runtime.db",
        workspaceRoot=tmp_path / "jobs",
    )
    try:
        loaded = service.LoadProject(
            runtime_pb2.LoadProjectRequest(project_path=str(projectDir)), None
        )
        assert loaded.ok is True
        operators = service.ListOperators(runtime_pb2.ListOperatorsRequest(), None)
        roiInfo = next(
            item
            for item in operators.operators
            if item.operator_id == "vision.preprocess.roi"
        )
        editor = json.loads(roiInfo.editor_spec_json)
        assert editor["previewMode"] == "pure"
        assert roiInfo.version == "1.1.0"

        assetReply = service.GetOperatorEditorAsset(
            runtime_pb2.GetOperatorEditorAssetRequest(
                operator_id="vision.preprocess.roi", version="1.1.0"
            ),
            None,
        )
        assert assetReply.ok is True
        assert hashlib.sha256(assetReply.content).hexdigest() == assetReply.sha256

        encodedOk, encoded = cv2.imencode(".png", image)
        assert encodedOk
        uploaded = service.UploadPreviewImage(
            iter(
                [
                    runtime_pb2.PreviewUploadChunk(
                        upload_id="upload-1",
                        filename="source.png",
                        content=encoded.tobytes(),
                        project_id=service.loadedProjectId,
                    )
                ]
            ),
            None,
        )
        assert uploaded.ok is True
        uploadedAsset = service.previewAssetStore.resolve(uploaded.asset_id)
        assert uploadedAsset is not None
        assert uploadedAsset.projectKey == service._loadedProjectPreviewKey
        preview = service.RunOperatorPreview(
            runtime_pb2.RunOperatorPreviewRequest(
                project_id=service.loadedProjectId,
                workflow_id="main",
                node_id="roi",
                operator_id="vision.preprocess.roi",
                image_asset_id=uploaded.asset_id,
                params_json=json.dumps(
                    {
                        "roiType": "bbox",
                        "x": 2.0,
                        "y": 3.0,
                        "width": 8.0,
                        "height": 7.0,
                        "padValue": 0,
                        "interpolation": "linear",
                    }
                ),
            ),
            None,
        )
        assert preview.ok is True
        assets = {item.port: item for item in preview.assets}
        assert {"croppedImage", "croppedMask", "maskedImage", "fullMask"} <= set(
            assets
        )
        croppedBytes = b"".join(
            chunk.content
            for chunk in service.StreamPreviewAsset(
                runtime_pb2.GetPreviewAssetRequest(
                    asset_id=assets["croppedImage"].asset_id,
                    project_id=service.loadedProjectId,
                ),
                None,
            )
        )
        cropped = cv2.imdecode(
            np.frombuffer(croppedBytes, dtype=np.uint8), cv2.IMREAD_UNCHANGED
        )
        assert cropped is not None
        assert cropped.shape[:2] == (7, 8)
        outputAsset = service.previewAssetStore.resolve(
            assets["croppedImage"].asset_id
        )
        assert outputAsset is not None
        assert outputAsset.projectKey == service._loadedProjectPreviewKey

        foreign = service.previewAssetStore.addTransientImage(
            image,
            projectKey="foreign-project-key",
        )
        assert list(
            service.StreamPreviewAsset(
                runtime_pb2.GetPreviewAssetRequest(
                    asset_id=foreign.assetId,
                    project_id=service.loadedProjectId,
                ),
                None,
            )
        ) == []
    finally:
        service.close()


def testSuccessfulJobPromotesCurrentAndDirectUpstreamSnapshots(tmp_path: Path) -> None:
    projectDir = tmp_path / "project"
    projectDir.mkdir()
    image = np.full((16, 18, 3), 80, dtype=np.uint8)
    imagePath = projectDir / "source.png"
    assert cv2.imwrite(str(imagePath), image)
    _writePreviewProject(projectDir, imagePath)
    service = RuntimeService(
        dbPath=tmp_path / "runtime.db",
        workspaceRoot=tmp_path / "jobs",
    )
    try:
        assert service.LoadProject(
            runtime_pb2.LoadProjectRequest(project_path=str(projectDir)), None
        ).ok
        started = service.StartJob(
            runtime_pb2.StartJobRequest(project_id=service.loadedProjectId), None
        )
        assert started.ok
        assert waitForTerminal(service, started.job_id).status == "COMPLETED"

        sources = service.ListNodePreviewSources(
            runtime_pb2.ListNodePreviewSourcesRequest(
                project_id=service.loadedProjectId,
                workflow_id="main",
                node_id="roi",
            ),
            None,
        )
        identities = {(item.source_kind, item.node_id, item.port) for item in sources.sources}
        assert ("upstream", "loader", "image") in identities
        assert ("current", "roi", "croppedImage") in identities
        assert all(item.iteration_path_json == "[]" for item in sources.sources)
    finally:
        service.close()


def testEditorAssetIsRegistrationSnapshot(tmp_path: Path) -> None:
    pluginRoot = tmp_path / "plugins"
    pluginDir = pluginRoot / "roi"
    (pluginDir / "ui").mkdir(parents=True)
    sourceDir = Path("src/emo_master/plugins/builtins/roi")
    shutil.copy2(sourceDir / "manifest.json", pluginDir / "manifest.json")
    shutil.copy2(sourceDir / "ui" / "editor.ui", pluginDir / "ui" / "editor.ui")
    service = RuntimeService(
        dbPath=tmp_path / "runtime.db",
        workspaceRoot=tmp_path / "jobs",
        pluginRootPaths=(str(pluginRoot),),
    )
    try:
        descriptor = service.pluginScanResult.activeOperators["vision.preprocess.roi"]
        assert descriptor.editorUiContent is not None
        frozen = descriptor.editorUiContent
        resource = pluginDir / "ui" / "editor.ui"
        resource.write_bytes(b"changed after registry scan")
        reply = service.GetOperatorEditorAsset(
            runtime_pb2.GetOperatorEditorAssetRequest(
                operator_id="vision.preprocess.roi", version="1.1.0"
            ),
            None,
        )
        assert reply.ok is True
        assert reply.content == frozen
    finally:
        service.close()
