from emo_master.core.workflow.loop_contracts import loopWorkflowReferenceFields

SESSION_OPERATOR = "communication.tcp.session"
SEND_OPERATOR = "communication.tcp.session_send"
MANAGED_TCP_OPERATORS = frozenset({SESSION_OPERATOR, SEND_OPERATOR, "communication.tcp.client", "communication.tcp.receive_once"})


class CommunicationDependencyError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def communicationNodes(document, workflowId):
    pending, visited, result = [workflowId], set(), []
    while pending:
        key = pending.pop()
        if key in visited:
            continue
        visited.add(key)
        for node in document.workflows[key].nodes:
            if node.operatorId in MANAGED_TCP_OPERATORS:
                result.append((key, node))
            if node.kind == "subflow" and node.targetWorkflowId:
                pending.append(node.targetWorkflowId)
            if node.kind == "loop":
                pending.extend(node.loop[field] for field in loopWorkflowReferenceFields(node.loop) if node.loop.get(field))
    return result


def validateCommunicationNode(document, node):
    # Current device bindings are checked by the compiler's shared binding rule.
    if node.operatorId in MANAGED_TCP_OPERATORS and not node.deviceBindings:
        raise CommunicationDependencyError("E_TCP_BINDING", "TCP 节点必须选择工程设备和通道")
