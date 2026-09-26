from __future__ import annotations

from copy import deepcopy
import json

import pytest

from emo_master.apps.designer.state.workflow_package import (
    WorkflowPackageDocument,
    buildWorkflowPackage,
    importWorkflowPackage,
    loadWorkflowPackage,
    previewWorkflowPackageImport,
    writeWorkflowPackage,
)
from emo_master.apps.designer.state.workflow_store import WorkflowStore


def _workflow(
    name: str,
    nodes: list[dict[str, object]] | None = None,
    inputs: dict[str, object] | None = None,
    outputs: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "name": name,
        "inputs": inputs or {},
        "outputs": outputs or {},
        "nodes": [
            {
                "nodeId": f"__workflow_input__:{name.lower()}",
                "kind": "workflow_input",
                "outputPorts": inputs or {},
            },
            {
                "nodeId": f"__workflow_output__:{name.lower()}",
                "kind": "workflow_output",
                "inputPorts": outputs or {},
            },
            *(nodes or []),
        ],
        "edges": [],
        "layout": {"nodePositions": {}},
    }


def _packageSourcePayload() -> dict[str, object]:
    return {
        "schemaVersion": "2.1",
        "project": {
            "projectId": "workflow-package-source",
            "name": "Package Source",
            "revision": 1,
            "createdAt": "2026-01-01T00:00:00Z",
            "updatedAt": "2026-01-01T00:00:00Z",
        },
        "entryWorkflowId": "main",
        "workflowOrder": ["main", "body", "condition", "unused"],
        "workflows": {
            "main": _workflow(
                "Main",
                [
                    {
                        "nodeId": "repeat",
                        "kind": "loop",
                        "inputPorts": {"value": "json"},
                        "outputPorts": {"result": "json"},
                        "loop": {
                            "contractVersion": 2,
                            "mode": "repeat",
                            "bodyWorkflowId": "body",
                            "repeatCount": 1,
                            "maxIterations": 1,
                            "timeoutMs": 0,
                        },
                    }
                ],
            ),
            "body": _workflow(
                "Body",
                [
                    {
                        "nodeId": "operator",
                        "kind": "operator",
                        "operatorId": "vision.test.echo",
                        "inputPorts": {"value": "json"},
                        "outputPorts": {"result": "json"},
                    },
                    {
                        "nodeId": "condition-call",
                        "kind": "subflow",
                        "targetWorkflowId": "condition",
                        "inputPorts": {"value": "json"},
                        "outputPorts": {"continue": "boolean"},
                    },
                ],
                inputs={"value": "json"},
                outputs={"result": "json"},
            ),
            "condition": _workflow(
                "Condition",
                inputs={"value": "json"},
                outputs={"continue": "boolean"},
            ),
            "unused": _workflow("Unused"),
        },
        "runtime": {},
        "dependencies": {"operators": []},
        "devices": {},
    }


def _whilePackageSourcePayload() -> dict[str, object]:
    payload = _packageSourcePayload()
    payload["workflowOrder"] = ["main", "body", "condition"]
    payload["workflows"] = {
        "main": _workflow(
            "Main",
            [
                {
                    "nodeId": "while",
                    "kind": "loop",
                    "inputPorts": {"count": "integer"},
                    "outputPorts": {"count": "integer"},
                    "loop": {
                        "contractVersion": 2,
                        "mode": "while",
                        "conditionWorkflowId": "condition",
                        "bodyWorkflowId": "body",
                        "maxIterations": 5,
                        "timeoutMs": 0,
                    },
                }
            ],
            inputs={"count": "integer"},
            outputs={"count": "integer"},
        ),
        "body": _workflow(
            "Body",
            inputs={"count": "integer"},
            outputs={"count": "integer"},
        ),
        "condition": _workflow(
            "Condition",
            inputs={"count": "integer"},
            outputs={"continue": "boolean"},
        ),
    }
    return payload


def testBuildWorkflowPackageIncludesRecursiveDependenciesOnly() -> None:
    store = WorkflowStore(_packageSourcePayload())

    package = buildWorkflowPackage(store, "main")

    assert package.rootWorkflowId == "main"
    assert package.workflowOrder == ["main", "body", "condition"]
    assert set(package.workflows) == {"main", "body", "condition"}
    assert package.requiredOperators == ["vision.test.echo"]


def testBuildWorkflowPackageDoesNotNormalizeUnrelatedSourceWorkflow() -> None:
    store = WorkflowStore(_packageSourcePayload())
    unrelated = store.get("unused")
    unrelated.nodes = [
        node
        for node in unrelated.nodes
        if node.get("kind") not in {"workflow_input", "workflow_output"}
    ]
    before = deepcopy(unrelated.nodes)

    buildWorkflowPackage(store, "main")

    assert unrelated.nodes == before


def testWorkflowPackageImportPreviewReportsConflictsDependenciesAndWarnings(
    tmp_path,
) -> None:
    payload = _packageSourcePayload()
    operator = payload["workflows"]["body"]["nodes"][2]
    missingPath = tmp_path / "missing.png"
    operator["paramSchema"] = {
        "type": "object",
        "properties": {
            "imagePath": {
                "type": "string",
                "xWidget": "file",
                "xFileMode": "open",
            },
            "outputPath": {
                "type": "string",
                "xWidget": "file",
                "xFileMode": "save",
            },
        },
    }
    operator["params"] = {
        "imagePath": str(missingPath),
        "outputPath": "outputs/result.png",
    }
    package = buildWorkflowPackage(WorkflowStore(payload), "main")
    target = WorkflowStore()
    before = deepcopy(target.workflows)

    preview = previewWorkflowPackageImport(
        target,
        package,
        availableOperatorIds=[],
    )

    assert preview.sourceRootWorkflowId == "main"
    assert preview.rootWorkflowId == "main-2"
    assert preview.workflowIdMap == {
        "main": "main-2",
        "body": "body",
        "condition": "condition",
    }
    assert [(item.sourceWorkflowId, item.workflowId) for item in preview.conflicts] == [
        ("main", "main-2")
    ]
    assert {item.relation for item in preview.dependencies} == {
        "Repeat · Body",
        "Subflow",
    }
    assert preview.missingOperators == ("vision.test.echo",)
    assert {
        (item.paramName, item.status) for item in preview.externalPaths
    } == {
        ("imagePath", "missing"),
        ("outputPath", "output"),
    }
    assert target.workflows == before

    result = importWorkflowPackage(target, package)
    assert result.workflowIdMap == preview.workflowIdMap


def testWorkflowPackageImportCanInsertMappedRootIntoParent() -> None:
    package = buildWorkflowPackage(
        WorkflowStore(_packageSourcePayload()), "body"
    )
    target = WorkflowStore()

    result = importWorkflowPackage(
        target,
        package,
        insertIntoWorkflowId="main",
    )

    assert result.rootWorkflowId == "body"
    assert result.parentWorkflowId == "main"
    assert result.insertedSubflowNodeId is not None
    assert target.activeWorkflowId == "main"
    assert target.entryWorkflowId == "main"
    inserted = next(
        node
        for node in target.get("main").nodes
        if node.get("nodeId") == result.insertedSubflowNodeId
    )
    assert inserted["kind"] == "subflow"
    assert inserted["targetWorkflowId"] == "body"
    assert inserted["inputPorts"] == {"value": "json"}
    assert inserted["outputPorts"] == {"result": "json"}
    assert result.insertedSubflowNodeId in target.get("main").layout["nodePositions"]


def testWorkflowPackageInsertFailureDoesNotMutateTarget() -> None:
    package = buildWorkflowPackage(
        WorkflowStore(_packageSourcePayload()), "body"
    )
    target = WorkflowStore()
    before = deepcopy(target.workflows)

    with pytest.raises(KeyError, match="missing-parent"):
        importWorkflowPackage(
            target,
            package,
            insertIntoWorkflowId="missing-parent",
        )

    assert target.workflows == before


def testWorkflowPackageRoundTripRemapsConflictingWorkflowReferences() -> None:
    source = WorkflowStore(_packageSourcePayload())
    package = buildWorkflowPackage(source, "main")
    target = WorkflowStore()
    originalEntry = target.entryWorkflowId

    first = importWorkflowPackage(target, package)
    second = importWorkflowPackage(target, package)

    assert originalEntry == target.entryWorkflowId == "main"
    assert first.rootWorkflowId == "main-2"
    assert second.rootWorkflowId == "main-3"
    assert first.workflowIdMap == {
        "main": "main-2",
        "body": "body",
        "condition": "condition",
    }
    assert second.workflowIdMap == {
        "main": "main-3",
        "body": "body-2",
        "condition": "condition-2",
    }
    firstRoot = target.get(first.rootWorkflowId)
    repeat = next(node for node in firstRoot.nodes if node.get("nodeId") == "repeat")
    assert repeat["loop"]["bodyWorkflowId"] == "body"
    firstBody = target.get("body")
    subflow = next(
        node for node in firstBody.nodes if node.get("nodeId") == "condition-call"
    )
    assert subflow["targetWorkflowId"] == "condition"
    assert target.activeWorkflowId == second.rootWorkflowId
    assert "vision.test.echo" in target.dependencies["operators"]


def testWorkflowPackageImportRewritesBoundaryNodeIdsAndLayout() -> None:
    source = WorkflowStore(_packageSourcePayload())
    sourceBody = source.get("body")
    inputNode = next(
        node for node in sourceBody.nodes if node.get("kind") == "workflow_input"
    )
    inputNodeId = str(inputNode["nodeId"])
    sourceBody.layout = {
        "nodePositions": {inputNodeId: {"x": 123.0, "y": 45.0}}
    }
    package = buildWorkflowPackage(source, "body")
    target = WorkflowStore()
    target.addWorkflow("Body", workflowId="body")

    result = importWorkflowPackage(target, package)

    imported = target.get(result.rootWorkflowId)
    importedInput = next(
        node for node in imported.nodes if node.get("kind") == "workflow_input"
    )
    assert importedInput["nodeId"] == "__workflow_input__:body-2"
    assert imported.layout["nodePositions"]["__workflow_input__:body-2"] == {
        "x": 123.0,
        "y": 45.0,
    }


def testWorkflowPackageImportRemapsWhileBodyAndConditionReferences() -> None:
    package = buildWorkflowPackage(
        WorkflowStore(_whilePackageSourcePayload()), "main"
    )
    target = WorkflowStore()
    target.addWorkflow(
        "Existing Body",
        workflowId="body",
        inputs={"count": "integer"},
        outputs={"count": "integer"},
    )
    target.addWorkflow(
        "Existing Condition",
        workflowId="condition",
        inputs={"count": "integer"},
        outputs={"continue": "boolean"},
    )

    result = importWorkflowPackage(target, package)

    assert result.workflowIdMap == {
        "main": "main-2",
        "body": "body-2",
        "condition": "condition-2",
    }
    importedRoot = target.get(result.rootWorkflowId)
    whileNode = next(
        node for node in importedRoot.nodes if node.get("nodeId") == "while"
    )
    assert whileNode["loop"]["bodyWorkflowId"] == "body-2"
    assert whileNode["loop"]["conditionWorkflowId"] == "condition-2"


def testWorkflowPackageFileRoundTripAddsDedicatedExtension(tmp_path) -> None:
    package = buildWorkflowPackage(WorkflowStore(_packageSourcePayload()), "body")

    writtenPath = writeWorkflowPackage(tmp_path / "body.json", package)
    loaded = loadWorkflowPackage(writtenPath)

    assert writtenPath.name == "body.emowf.json"
    assert loaded.toPayload() == package.toPayload()


def testWorkflowPackageRejectsDanglingReferenceWithoutMutatingTarget() -> None:
    package = buildWorkflowPackage(WorkflowStore(_packageSourcePayload()), "body")
    payload = package.toPayload()
    subflow = next(
        node
        for node in payload["workflows"]["body"]["nodes"]
        if node.get("kind") == "subflow"
    )
    subflow["targetWorkflowId"] = "missing"
    target = WorkflowStore()
    before = deepcopy(target.workflows)

    with pytest.raises(ValueError, match="packaged workflow|existing workflow"):
        invalid = WorkflowPackageDocument.model_validate(payload)
        importWorkflowPackage(target, invalid)

    assert target.workflows == before


def testWorkflowPackageRejectsOperatorManifestMismatch(tmp_path) -> None:
    package = buildWorkflowPackage(WorkflowStore(_packageSourcePayload()), "body")
    payload = package.toPayload()
    payload["requiredOperators"] = []
    packagePath = tmp_path / "invalid.emowf.json"
    packagePath.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="requiredOperators"):
        loadWorkflowPackage(packagePath)
