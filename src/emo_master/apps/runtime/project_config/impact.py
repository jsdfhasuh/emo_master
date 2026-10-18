"""Conservative dependency-scoped retirement for non-cycle-safe changes."""
from emo_master.core.project.configuration import projectChanges, presentationExecution
from emo_master.core.project.models import ProjectDocument
from emo_master.core.workflow.loop_contracts import loopWorkflowReferenceFields


def affectedJobs(runtime, payload):
    before = runtime.projectConfiguration.current["payload"]
    flows, devices = set(), set()
    for change in projectChanges(before, payload):
        p = change["parts"]
        if p[0] in {"project", "workflowOrder"}:
            continue
        if p[0] == "workflows":
            if len(p) >= 3 and p[2] in {"layout", "name"}:
                continue
            if len(p) >= 5 and p[2] == "nodes" and p[4] == "displayName":
                continue
            flows.add(p[1])
        elif p[:2] == ("devices", "definitions") and len(p) > 2:
            devices.add(p[2])
        elif p[0] == "resources":
            oldResources, newResources = before.get("resources") or {}, payload.get("resources") or {}
            for document in (before, payload):
                resources = document.get("resources") or {}
                fields = (p[1],) if len(p) >= 2 and p[1] in {"parameterBindings", "siteBindings"} else ("parameterBindings", "siteBindings")
                for binding in [binding for field in fields for binding in resources.get(field, [])]:
                    if len(p) >= 3 and p[1] == "items" and binding.get("resourceId") != p[2]:
                        continue
                    if len(p) >= 2 and p[1] in {"parameterBindings", "siteBindings"}:
                        if binding in oldResources.get(p[1], []) and binding in newResources.get(p[1], []):
                            continue
                    target = binding["target"]
                    if "deviceId" in target:
                        devices.add(target["deviceId"])
                    else:
                        flows.add(target["workflowId"])
        elif p[0] == "presentation":
            if presentationExecution(before) == presentationExecution(payload):
                continue
            for document in (before, payload):
                flows.update(scope["entryWorkflowId"] for scope in
                             (document.get("presentation") or {}).get("resultScopes", {}).values())
        elif p[0] == "communication":
            from emo_master.core.project.tcp_dependencies import MANAGED_TCP_OPERATORS
            for document in (before, payload):
                flows.update(key for key, flow in document["workflows"].items()
                             if any(node.get("operatorId") in MANAGED_TCP_OPERATORS for node in flow["nodes"]))
        else:
            return {record.jobId for record in runtime.jobRepository.all()}
    edges = {}
    for data in (before, payload):
        document = ProjectDocument.model_validate(data)
        for key, workflow in document.workflows.items():
            targets = edges.setdefault(key, set())
            for node in workflow.nodes:
                if any(binding.deviceId in devices for binding in node.deviceBindings.values()):
                    flows.add(key)
                if node.kind == "subflow" and node.targetWorkflowId:
                    targets.add(node.targetWorkflowId)
                if node.kind == "loop":
                    targets.update(node.loop[f] for f in loopWorkflowReferenceFields(node.loop) if node.loop.get(f))
                if node.kind == "dispatch":
                    targets.update(t["workflowId"] for t in node.params["targets"])
    while True:
        expanded = flows | {key for key, targets in edges.items() if targets & flows}
        if expanded == flows:
            break
        flows = expanded
    jobs = {record.jobId for record in runtime.jobRepository.all() if record.workflowId in flows}
    for owner in tuple(jobs):
        group = runtime.deviceManager.dispatchGroups.get(owner)
        if group is not None:
            jobs.update(group.members)
    return jobs
