"""Snapshot-fenced starts, independent of the editable Designer session."""
from uuid import uuid4


def startOnline(client, snapshot):
    if not snapshot.get("versionedStart") or snapshot.get("recoveryError"):
        raise ValueError("Runtime does not support ready, snapshot-fenced starts")
    payload = snapshot["payload"]
    targets = list(dict.fromkeys(payload.get("runtime", {}).get("startupWorkflowIds") or [payload["entryWorkflowId"]]))
    if not targets or any(key not in payload["workflows"] for key in targets):
        raise ValueError("online startup workflow is unavailable")
    results = []
    for workflowId in targets:
        requestId = uuid4().hex
        try:
            reply = client.startJob(payload["project"]["projectId"], workflowId,
                startRequestId=requestId, expectedRuntimeInstanceId=snapshot["runtimeInstanceId"],
                expectedDocumentRevision=snapshot["documentRevision"])
            results.append(dict(workflowId=workflowId, requestId=requestId, reply=reply))
        except Exception as error:
            results.append(dict(workflowId=workflowId, requestId=requestId, error=str(error)))
            break  # Reconcile this request before starting any remaining targets.
    return results
