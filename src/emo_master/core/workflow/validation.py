from __future__ import annotations

from collections.abc import Mapping

from emo_master.core.contracts.port_compatibility import arePortTypesCompatible
from emo_master.core.project.models import ProjectDocument
from emo_master.core.workflow.errors import ValidationIssue
from emo_master.core.workflow.loop_contracts import (
    CURRENT_LOOP_CONTRACT_VERSION,
    deriveLoopContract,
    interfacePortTypes,
)


def validateProjectDocument(
    document: ProjectDocument, operatorRegistry: Mapping[str, object] | None = None
) -> list[ValidationIssue]:
    """Perform inexpensive structural validation before compilation."""
    issues: list[ValidationIssue] = []
    projectId = document.project.projectId
    workflowIds = set(document.workflows)
    if document.entryWorkflowId not in workflowIds:
        issues.append(
            ValidationIssue(
                "E_ENTRY_WORKFLOW_UNKNOWN",
                f"entry workflow does not exist: {document.entryWorkflowId}",
                projectId=projectId,
            )
        )
    if len(set(document.workflowOrder)) != len(document.workflowOrder):
        issues.append(
            ValidationIssue(
                "E_WORKFLOW_ORDER_DUPLICATE",
                "workflowOrder contains duplicate workflow ids",
                projectId=projectId,
            )
        )
    if set(document.workflowOrder) != workflowIds:
        issues.append(
            ValidationIssue(
                "E_WORKFLOW_ORDER_INCOMPLETE",
                "workflowOrder must cover every workflow exactly once",
                projectId=projectId,
            )
        )

    for workflowId, workflow in document.workflows.items():
        nodeIds = [node.nodeId for node in workflow.nodes]
        duplicateIds = {nodeId for nodeId in nodeIds if nodeIds.count(nodeId) > 1}
        for nodeId in sorted(duplicateIds):
            issues.append(
                ValidationIssue(
                    "E_NODE_ID_DUPLICATE",
                    f"duplicate node id: {nodeId}",
                    projectId=projectId,
                    workflowId=workflowId,
                    nodeId=nodeId,
                )
            )
        inputNodes = [node for node in workflow.nodes if node.kind == "workflow_input"]
        outputNodes = [node for node in workflow.nodes if node.kind == "workflow_output"]
        if len(inputNodes) != 1:
            issues.append(
                ValidationIssue(
                    "E_WORKFLOW_INPUT_COUNT",
                    "workflow must contain exactly one workflow_input node",
                    projectId=projectId,
                    workflowId=workflowId,
                )
            )
        if len(outputNodes) != 1:
            issues.append(
                ValidationIssue(
                    "E_WORKFLOW_OUTPUT_COUNT",
                    "workflow must contain exactly one workflow_output node",
                    projectId=projectId,
                    workflowId=workflowId,
                )
            )
        for node in workflow.nodes:
            if node.kind == "operator" and not node.operatorId:
                issues.append(
                    ValidationIssue(
                        "E_OPERATOR_ID_MISSING",
                        "operator node requires operatorId",
                        projectId=projectId,
                        workflowId=workflowId,
                        nodeId=node.nodeId,
                    )
                )
            if (
                node.kind == "subflow"
                and (not node.targetWorkflowId or node.targetWorkflowId not in workflowIds)
            ):
                issues.append(
                    ValidationIssue(
                        "E_SUBFLOW_TARGET_UNKNOWN",
                        "subflow target workflow does not exist",
                        projectId=projectId,
                        workflowId=workflowId,
                        nodeId=node.nodeId,
                        fieldPath="targetWorkflowId",
                    )
                )
            if node.kind == "loop":
                mode = node.loop.get("mode")
                if mode not in {"repeat", "foreach", "while"}:
                    issues.append(
                        ValidationIssue(
                            "E_LOOP_MODE_INVALID",
                            "loop mode must be repeat, foreach or while",
                            projectId=projectId,
                            workflowId=workflowId,
                            nodeId=node.nodeId,
                        )
                    )
                for field in (
                    "bodyWorkflowId",
                    "conditionWorkflowId" if mode == "while" else "__unused__",
                ):
                    if field == "__unused__":
                        continue
                    target = node.loop.get(field)
                    if not isinstance(target, str) or target not in workflowIds:
                        issues.append(
                            ValidationIssue(
                                "E_LOOP_TARGET_UNKNOWN",
                                f"loop target workflow does not exist: {field}",
                                projectId=projectId,
                                workflowId=workflowId,
                                nodeId=node.nodeId,
                                fieldPath=f"loop.{field}",
                            )
                        )
                for limitField in ("maxIterations",):
                    limit = node.loop.get(limitField)
                    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 0:
                        issues.append(
                            ValidationIssue(
                                "E_LOOP_LIMIT_INVALID",
                                f"{limitField} must be a non-negative integer",
                                projectId=projectId,
                                workflowId=workflowId,
                                nodeId=node.nodeId,
                                fieldPath=f"loop.{limitField}",
                            )
                        )
                if mode == "repeat":
                    repeatCount = node.loop.get("repeatCount")
                    if (
                        not isinstance(repeatCount, int)
                        or isinstance(repeatCount, bool)
                        or repeatCount < 0
                    ):
                        issues.append(
                            ValidationIssue(
                                "E_LOOP_REPEAT_COUNT_INVALID",
                                "repeatCount must be a non-negative integer",
                                projectId=projectId,
                                workflowId=workflowId,
                                nodeId=node.nodeId,
                                fieldPath="loop.repeatCount",
                            )
                        )
                timeout = node.loop.get("timeoutMs", 0)
                if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout < 0:
                    issues.append(
                        ValidationIssue(
                            "E_LOOP_TIMEOUT_INVALID",
                            "timeoutMs must be a non-negative integer",
                            projectId=projectId,
                            workflowId=workflowId,
                            nodeId=node.nodeId,
                            fieldPath="loop.timeoutMs",
                        )
                    )
                if (
                    node.loop.get("contractVersion") == CURRENT_LOOP_CONTRACT_VERSION
                    and document.schemaVersion != "2.1"
                ):
                    issues.append(
                        ValidationIssue(
                            "E_LOOP_CONTRACT_SCHEMA_UNSUPPORTED",
                            "loop contractVersion=2 requires project schemaVersion 2.1",
                            projectId=projectId,
                            workflowId=workflowId,
                            nodeId=node.nodeId,
                            fieldPath="loop.contractVersion",
                        )
                    )
                bodyWorkflowId = node.loop.get("bodyWorkflowId")
                conditionWorkflowId = node.loop.get("conditionWorkflowId")
                bodyWorkflow = (
                    document.workflows.get(bodyWorkflowId)
                    if isinstance(bodyWorkflowId, str)
                    else None
                )
                conditionWorkflow = (
                    document.workflows.get(conditionWorkflowId)
                    if isinstance(conditionWorkflowId, str)
                    else None
                )
                if bodyWorkflow is not None and (
                    mode != "while" or conditionWorkflow is not None
                ):
                    contract = deriveLoopContract(
                        node.loop,
                        bodyWorkflow.inputs,
                        bodyWorkflow.outputs,
                        conditionWorkflow.inputs if conditionWorkflow is not None else {},
                        conditionWorkflow.outputs if conditionWorkflow is not None else {},
                    )
                    for contractIssue in contract.issues:
                        if contractIssue.code == "E_LOOP_MODE_INVALID":
                            continue
                        issues.append(
                            ValidationIssue(
                                contractIssue.code,
                                contractIssue.message,
                                projectId=projectId,
                                workflowId=workflowId,
                                nodeId=node.nodeId,
                                fieldPath=contractIssue.fieldPath,
                            )
                        )
                    actualInputs = interfacePortTypes(node.inputPorts)
                    actualOutputs = interfacePortTypes(node.outputPorts)
                    if mode == "repeat" and node.loop.get("repeatCount") == 0:
                        impossibleActualOutputs = sorted(
                            name
                            for name, outputType in actualOutputs.items()
                            if name not in actualInputs
                            or not arePortTypesCompatible(
                                actualInputs[name], outputType
                            )
                        )
                        if impossibleActualOutputs and not any(
                            issue.code == "E_LOOP_ZERO_OUTPUT_UNSATISFIABLE"
                            for issue in issues
                            if issue.workflowId == workflowId
                            and issue.nodeId == node.nodeId
                        ):
                            issues.append(
                                ValidationIssue(
                                    "E_LOOP_ZERO_OUTPUT_UNSATISFIABLE",
                                    "repeatCount=0 cannot pass through loop outputs: "
                                    + ", ".join(impossibleActualOutputs),
                                    projectId=projectId,
                                    workflowId=workflowId,
                                    nodeId=node.nodeId,
                                    fieldPath="loop.repeatCount",
                                )
                            )
                    if (
                        actualInputs != contract.inputPorts
                        or actualOutputs != contract.outputPorts
                    ):
                        issues.append(
                            ValidationIssue(
                                "E_LOOP_PORTS_STALE",
                                "loop ports do not match the referenced workflow contract",
                                projectId=projectId,
                                workflowId=workflowId,
                                nodeId=node.nodeId,
                                fieldPath="inputPorts/outputPorts",
                            )
                        )
            if node.kind == "operator" and node.operatorId and operatorRegistry is not None:
                if node.operatorId not in operatorRegistry:
                    issues.append(
                        ValidationIssue(
                            "E_OPERATOR_UNKNOWN",
                            f"operator is not registered: {node.operatorId}",
                            projectId=projectId,
                            workflowId=workflowId,
                            nodeId=node.nodeId,
                            fieldPath="operatorId",
                        )
                    )
        nodeSet = set(nodeIds)
        for edge in workflow.edges:
            if edge.fromNode not in nodeSet or edge.toNode not in nodeSet:
                issues.append(
                    ValidationIssue(
                        "E_EDGE_NODE_UNKNOWN",
                        "edge references an unknown node",
                        projectId=projectId,
                        workflowId=workflowId,
                        fieldPath="edges",
                    )
                )
    return issues
