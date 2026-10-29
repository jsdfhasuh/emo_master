"""Conservative dependency-scoped retirement for non-cycle-safe changes."""
from emo_master.core.project.configuration import projectChanges, presentationExecution
from emo_master.core.workflow.loop_contracts import loopWorkflowReferenceFields


def affectedObjects(before, payload):
    if before["project"]["projectId"] != payload["project"]["projectId"]:
        return (set(before['workflows']) | set(payload['workflows']),
                set(before['devices']['definitions']) | set(payload['devices']['definitions']), True)
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
            return set(before['workflows']) | set(payload['workflows']), devices, True
    edges = {}
    for data in (before, payload):
        for key, workflow in data['workflows'].items():
            targets = edges.setdefault(key, set())
            for node in workflow['nodes']:
                if any(binding['deviceId'] in devices for binding in node.get('deviceBindings', {}).values()):
                    flows.add(key)
                if node.get('kind') == "subflow" and node.get('targetWorkflowId'):
                    targets.add(node['targetWorkflowId'])
                if node.get('kind') == "loop":
                    config = node.get('loop') or {}
                    targets.update(config[f] for f in loopWorkflowReferenceFields(config) if config.get(f))
                if node.get('kind') == "dispatch":
                    targets.update(t['workflowId'] for t in node.get('params', {}).get('targets', []))
    while True:
        expanded = flows | {key for key, targets in edges.items() if targets & flows}
        if expanded == flows:
            break
        flows = expanded
    return flows, devices, False


def affectedJobs(runtime, payload, *, sourceChanged=False):
    flows, _devices, allJobs = affectedObjects(runtime.projectConfiguration.current['payload'], payload)
    jobs = {record.jobId for record in runtime.jobRepository.all() if allJobs or record.workflowId in flows}
    if sourceChanged:
        projectId = runtime.projectConfiguration.current['payload']['project']['projectId']
        jobs.update(record.jobId for record in runtime.jobRepository.all() if record.projectId == projectId)
    for owner in tuple(jobs):
        group = runtime.deviceManager.dispatchGroups.get(owner)
        if group is not None:
            jobs.update(group.members)
    return jobs


def sourceCycleOwners(runtime):
    """Only leases of the current project depend on its source directory."""
    configuration = runtime.projectConfiguration
    projectId = configuration.current['payload']['project']['projectId']
    with configuration.cycleLock:
        leases = [*configuration.cycles.items(),
                  *((owner, lease) for owner, children in configuration.childCycles.items() for lease in children.values())]
        return {owner for owner, lease in leases if lease['snapshot']['payload']['project']['projectId'] == projectId}


def changeEffects(runtime, candidate):
    """Describe explicit preflight changes using the same cycle/replacement rules."""
    from emo_master.core.project.configuration import executionDigest, selectedCandidate
    before = runtime.projectConfiguration.current['payload']
    projectChanged = before['project']['projectId'] != candidate['project']['projectId']
    result = {}
    for change in projectChanges(before, candidate):
        partial = selectedCandidate(before, candidate, [change['path']])
        scope = candidate if projectChanged else partial
        flows, devices, _all = affectedObjects(before, scope)
        if projectChanged:
            effect = 'stop'
        elif executionDigest(partial) == executionDigest(before):
            effect = 'document'
        elif runtime._supportsCycleChange(partial):
            effect = 'next_cycle'
        elif devices and change['parts'][0] in {'devices', 'resources'}:
            effect = 'reconnect' if any(key in before['devices']['definitions'] for key in devices) else 'idle'
        else:
            effect = 'stop'
        affected = affectedJobs(runtime, scope)
        jobs = sorted(record.jobId for record in runtime.jobRepository.all() if record.jobId in affected
                      and (not record.isTerminal or runtime.jobSupervisor.ownsJobResources(record.jobId)))
        result[change['path']] = dict(effect=effect, devices=sorted(devices), workflows=sorted(flows), jobs=jobs)
    return result
