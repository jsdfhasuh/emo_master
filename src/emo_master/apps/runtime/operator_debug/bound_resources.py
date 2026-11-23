"""Resolve input files bound to the single node in an operator debug session."""
from pathlib import Path

from emo_master.apps.runtime.operator_debug.contracts import DebugError
from emo_master.core.project.node_input_files import nodeInputPathKey
from emo_master.core.project.snapshots import verifyResourceItems
from emo_master.core.workflow.errors import WorkflowCompileError


def uploadedNodeInputs(projectId, inventory, document):
    """Remote debug may read the verified bytes for a matching draft path and digest."""
    if document is None or document.project.projectId != projectId or not document.resources:
        return {}
    declared = {nodeInputPathKey(item.get("path")): item.get("sha256") for item in inventory.values()
                if isinstance(item, dict)}
    paths = {}
    for key, path in document._verifiedResourcePaths.items():
        item = document.resources.items[key]
        relative = nodeInputPathKey(item.path)
        if relative is not None and declared.get(relative) == item.sha256:
            paths[relative] = path
    return paths


def _loadedPaths(spec, plan, document):
    if document is None or document.project.projectId != spec["projectId"] or not document.resources:
        return {}
    workflow = document.workflows.get(spec["workflowId"])
    if workflow is None:
        return {}
    node = next((node for node in workflow.nodes if node.nodeId == spec["nodeId"]), None)
    if node is None or node.operatorId != spec["operatorId"]:
        return {}
    paths = {}
    for binding in plan.nodeParameterBindings:
        key = binding.resourceId
        if (binding not in document.resources.nodeParameterBindings
                or document.resources.items.get(key) != plan.items[key]):
            continue
        path = node.params.get(binding.target.parameterPath[0])
        if isinstance(path, str) and Path(path).is_absolute():
            paths[key] = path
    return paths


def resolveBoundResources(spec, plan, loadedDocument, *, preferProjectFiles: bool) -> dict[str, str]:
    root = Path(spec["resourceRoot"]) if spec.get("resourceRoot") else None
    paths = _loadedPaths(spec, plan, loadedDocument)
    if preferProjectFiles and root is not None:
        # The open Designer project may contain newer inputs than the last Runtime configuration.
        paths = {key: path for key, path in paths.items() if not (root / plan.items[key].path).exists()}
    try:
        resolved = verifyResourceItems(spec["projectId"], plan.items, root, resourcePaths=paths)
    except WorkflowCompileError as error:
        issue = error.issues[0]
        raise DebugError(issue.code, issue.message) from error
    return {binding.target.parameterPath[0]: resolved[binding.resourceId] for binding in plan.nodeParameterBindings}
