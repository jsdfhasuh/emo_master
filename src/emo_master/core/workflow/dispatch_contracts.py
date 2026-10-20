"""Explicit, local-runtime fan-out contracts shared by compiler and admission."""
from emo_master.core.contracts.port_compatibility import isPortTypeAssignable
from emo_master.core.workflow.loop_contracts import interfacePortTypes, loopWorkflowReferenceFields


def dispatchTargets(node):
    targets = node.params.get("targets", [])
    if not isinstance(targets, list) or not targets or len(targets) > 32:
        raise ValueError("Dispatch requires 1..32 explicit target slots")
    seen = set()
    for target in targets:
        if not isinstance(target, dict) or set(target) - {"targetSlotId", "workflowId", "inputs", "queueCapacity"}:
            raise ValueError("invalid Dispatch target declaration")
        slot = target.get("targetSlotId")
        if not isinstance(slot, str) or not slot or len(slot) > 80 or slot in seen:
            raise ValueError("Dispatch target slots must be nonempty and unique")
        seen.add(slot)
        if not isinstance(target.get("workflowId"), str) or not isinstance(target.get("inputs"), dict):
            raise ValueError("Dispatch requires workflowId and typed input mapping")
        capacity = target.get("queueCapacity", 4)
        if type(capacity) is not int or not 1 <= capacity <= 64:
            raise ValueError("Dispatch queue capacity must be 1..64")
    return targets


def dispatchPorts(document, workflowId, node):
    if document.schemaVersion not in {"2.6", "2.7"}:
        raise ValueError("Dispatch/Join requires project schema 2.6")
    if node.kind == "join":
        source = next((n for n in document.workflows[workflowId].nodes
                       if n.nodeId == node.params.get("dispatchNodeId") and n.kind == "dispatch"), None)
        if source is None:
            raise ValueError("Join must reference a Dispatch in the same workflow")
        timeout = node.params.get("timeoutMs", 30000)
        if type(timeout) is not int or not 1 <= timeout <= 1200000:
            raise ValueError("Join timeout must be 1..1200000 ms")
        return {"token": "object"}, {t["targetSlotId"]: "object" for t in dispatchTargets(source)}
    for target in dispatchTargets(node):
        workflow = document.workflows.get(target["workflowId"])
        if workflow is None:
            raise ValueError("Dispatch target workflow does not exist: " + target["workflowId"])
        ports = interfacePortTypes(workflow.inputs)
        if set(target["inputs"]) != set(ports):
            raise ValueError("Dispatch input mapping must cover exactly the target interface")
        for port, source in target["inputs"].items():
            if not isinstance(source, str) or source not in node.inputPorts:
                raise ValueError("Dispatch mapping references an unknown input")
            if not isPortTypeAssignable(node.inputPorts[source], ports[port]):
                raise ValueError("Dispatch input type mismatch: " + port)
    return dict(node.inputPorts), {"token": "object"}


def groupSlots(document, workflowId):
    """Collect all reachable targets, including synchronous calls and loops."""
    slots, visiting, visited = {}, set(), set()

    def visit(key):
        if key in visiting:
            raise ValueError("E_DISPATCH_RECURSIVE: recursive run-group dependency")
        if key in visited:
            return
        visiting.add(key)
        for node in document.workflows[key].nodes:
            if node.kind == "dispatch":
                dispatchPorts(document, key, node)
                for target in dispatchTargets(node):
                    slot = target["targetSlotId"]
                    identity = (target["workflowId"], target.get("queueCapacity", 4))
                    if slot in slots and slots[slot] != identity:
                        raise ValueError("E_DISPATCH_SLOT_CONFLICT: slot workflow/capacity differs")
                    slots[slot] = identity
                    visit(target["workflowId"])
            elif node.kind == "subflow" and node.targetWorkflowId:
                visit(node.targetWorkflowId)
            elif node.kind == "loop":
                for field in loopWorkflowReferenceFields(node.loop):
                    if node.loop.get(field):
                        visit(node.loop[field])
        visiting.remove(key)
        visited.add(key)
    visit(workflowId)
    return slots
