"""No-device example: acquire a session BEFORE invoking the calculation subflow.

The two channels are illustrative loopback routes, not a two-station scheduler.
Nothing in this file opens sockets or sends data.
"""
from emo_master.core.project.migration import migrateProjectPayload
from emo_master.core.project.models import ProjectDocument


def sampleProject(port=10000, *, enabled=False, startupPolicy="manual"):
    payload = migrateProjectPayload(dict(schemaVersion="2.1", project=dict(projectId="tcp-example", name="项目 TCP 示例",
        revision=1, createdAt="2026-10-15T00:00:00Z", updatedAt="2026-10-15T00:00:00Z"),
        entryWorkflowId="channel-a", workflowOrder=["channel-a", "channel-b", "calculate"], workflows={
            key: dict(name=key, nodes=[dict(nodeId="input", kind="workflow_input"),
                                     dict(nodeId="output", kind="workflow_output")], edges=[])
            for key in ("channel-a", "channel-b", "calculate")}), enableCommunication=True)
    payload["communication"] = dict(enabled=enabled, startupPolicy=startupPolicy, tcpServer=dict(port=port,
        channels=[dict(channelId=key, name=name, allowedIps=[ip]) for key, name, ip in
                  (("channel-a", "客户端 A", "127.0.0.1"), ("channel-b", "客户端 B", "127.0.0.2"))]))
    payload["runtime"]["startupWorkflowIds"] = ["channel-a", "channel-b"]
    for channel in ("channel-a", "channel-b"):
        payload["workflows"][channel] = dict(name=channel, inputs=dict(x="number", y="number"),
            outputs=dict(receipt="tcpSendReceipt"), nodes=[dict(nodeId="input", kind="workflow_input"),
                dict(nodeId="session", operatorId="communication.tcp.session", params=dict(channelId=channel)),
                dict(nodeId="calculate", kind="subflow", targetWorkflowId="calculate"),
                dict(nodeId="send", operatorId="communication.tcp.session_send"),
                dict(nodeId="output", kind="workflow_output")], edges=[
                    dict(fromNode="input", fromPort=p, toNode="calculate", toPort=p) for p in ("x", "y")] + [
                    dict(fromNode="session", fromPort="session", toNode="calculate", toPort="session"),
                    dict(fromNode="session", fromPort="session", toNode="send", toPort="session"),
                    dict(fromNode="calculate", fromPort="data", toNode="send", toPort="data"),
                    dict(fromNode="send", fromPort="receipt", toNode="output", toPort="receipt")])
    payload["workflows"]["calculate"] = dict(name="采集/计算入口（本示例仅格式化）",
        inputs=dict(session="tcpSessionRef", x="number", y="number"), outputs=dict(data="string"), nodes=[
            dict(nodeId="input", kind="workflow_input"),
            dict(nodeId="format", operatorId="communication.coordinate_message_format"),
            dict(nodeId="output", kind="workflow_output")], edges=[
                dict(fromNode="input", fromPort=p, toNode="format", toPort=p) for p in ("x", "y")] + [
                dict(fromNode="format", fromPort="data", toNode="output", toPort="data")])
    return ProjectDocument.model_validate(payload)
