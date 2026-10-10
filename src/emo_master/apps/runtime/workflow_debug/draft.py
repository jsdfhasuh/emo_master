from emo_master.apps.runtime.operator_debug.contracts import (
    MAX_REQUEST_BYTES, draft, digest, fail, identifier, parse,
)
from emo_master.core.project.models import ProjectDocument
from emo_master.core.project.migration import migrateProjectPayload


def workflowDraft(request, admitted):
    payload = parse(request["projectJson"], MAX_REQUEST_BYTES)
    projectId, workflowId = identifier(request["projectId"]), identifier(request["workflowId"])
    document = ProjectDocument.model_validate(migrateProjectPayload(payload))
    if document.project.projectId != projectId or workflowId not in document.workflows:
        fail("E_DEBUG_CONTEXT_INVALID", "workflow draft identity changed")
    payload = document.toPayload()
    used = set()
    # Admission precedes compilation: validators and constructors are plugin code.
    for key, workflow in document.workflows.items():
        for node in workflow.nodes:
            if node.kind == "operator":
                draft(request["projectJson"], projectId, key, node.nodeId, node.operatorId, admitted)
                used.add(node.operatorId)
    if request.get("resourceRoot"):
        fail("E_DEBUG_UNSUPPORTED", "workflow resource roots are not admitted")
    return dict(kind="workflow", operatorId="", nodeId="", projectId=projectId, workflowId=workflowId,
                draftDigest=digest(payload), project=payload, entries={key: admitted[key] for key in sorted(used)},
                inputPorts=document.workflows[workflowId].inputs, outputPorts=document.workflows[workflowId].outputs,
                paramSchema={}, variableDefinitions={key: value.model_dump() for key, value in document.globalVariables.items()})
