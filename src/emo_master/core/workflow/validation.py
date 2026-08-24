from __future__ import annotations

from collections.abc import Mapping

from emo_master.core.project.models import ProjectDocument
from emo_master.core.workflow.errors import ValidationIssue


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
