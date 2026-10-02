"""Freeze optional display metadata without changing normal execution semantics.

This is deliberately not debug preparation: installed operators, original node
parameters, output paths, production counters and the normal Job snapshot remain
owned by RuntimeService.StartJob. No device or operator is executed here.
"""
from dataclasses import dataclass

from emo_master.core.presentation.models import walkComponents
from emo_master.core.presentation.capture_limits import normalCaptureLimits
from emo_master.core.presentation.validation import validateBindings
from emo_master.core.project.snapshots import canonicalJson, captureDefinition, revisionOf, verifyResources


@dataclass(frozen=True)
class NormalCapture:
    sourceJson: str
    captureDefinitionJson: str
    capturePlanRevision: str
    executionRevision: str
    limitsJson: str


def freezeNormalCapture(document, registry, resourceRoot, workflowId):
    if document.presentation is None:
        raise ValueError("presentation must be explicitly enabled and saved")
    if workflowId != document.entryWorkflowId:
        raise ValueError("normal presentation capture requires the project entry workflow")
    issues = validateBindings(document, {key: value.manifest for key, value in registry.items()})
    if issues:
        raise ValueError("; ".join(f"{issue.path}: {issue.message}" for issue in issues))
    if document.resources is not None:
        # Check declared package assets, but never rewrite the original run's
        # site/file parameters or move it into the debug data namespace.
        verifyResources(document, resourceRoot)
    presentation = document.presentation
    capture = captureDefinition(presentation)
    used = {sourceId for page in presentation.pages.values()
            for component in walkComponents(page.components)
            for sourceId in component.bindings.values()}
    sources = {key: presentation.dataSources[key].model_dump() for key in sorted(used)}
    scopes = capture["scopes"]
    limits = normalCaptureLimits(presentation)
    for sourceId, source in sources.items():
        scope = scopes[source["resultScopeId"]]
        if source["kind"] == "runtime_status":
            raise ValueError(
                f"{sourceId}: runtime_status is not a captured workflow output; "
                "use the page's native live Job status label for execution state; "
                "an unbound runtime_status component shows connection state"
            )
        if (source["kind"] not in {"node_output", "workflow_output"}
                or source["workflowId"] != scope["scopeWorkflowId"]
                or source["callPath"] != scope["callPath"]):
            raise ValueError(f"{sourceId}: output must belong to its explicit invocation scope")
    execution = {"projectId": document.project.projectId,
                 "workflowId": workflowId,
                 "workflows": {key: value.model_dump() for key, value in document.workflows.items()},
                 "plugins": {node.operatorId: registry[node.operatorId].manifest.version
                             for workflow in document.workflows.values() for node in workflow.nodes
                             if node.kind == "operator"}}
    return NormalCapture(canonicalJson({"sources": sources, "scopes": scopes}),
                         canonicalJson(capture), revisionOf(capture), revisionOf(execution),
                         canonicalJson(limits))
