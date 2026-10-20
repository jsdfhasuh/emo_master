from emo_master.core.project.communication import projectCommunication
from emo_master.core.workflow.loop_contracts import loopWorkflowReferenceFields

SESSION_OPERATOR = "communication.tcp.session"
SEND_OPERATOR = "communication.tcp.session_send"
MANAGED_TCP_OPERATORS = frozenset({SESSION_OPERATOR, SEND_OPERATOR})


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
    if node.operatorId != SESSION_OPERATOR:
        return
    config = projectCommunication(document)
    service = node.params.get("serviceId", "tcp-server")
    channel = node.params.get("channelId", "")
    if service != config.tcpServer.serviceId or channel not in {c.channelId for c in config.tcpServer.channels}:
        raise CommunicationDependencyError("E_TCP_CHANNEL_MISSING", "所选 TCP 服务或通道不存在，请到项目通信设置配置")
    if any(binding.parameterPath[0] in {"serviceId", "channelId"} for binding in node.globalVariableBindings if binding.parameterPath):
        raise CommunicationDependencyError("E_TCP_BINDING", "TCP 服务和通道必须是静态项目资源引用")
