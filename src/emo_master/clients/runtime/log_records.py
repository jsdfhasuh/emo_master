"""Runtime log wire conversion, query and paging."""
import json

from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.core.log_export import LogExportCancelled, exportLogs
from emo_master.core.runtime_log_records import StructuredLogEntry


def eventPayload(event):
    fields = {'jobId': 'job_id', 'eventType': 'event_type', 'message': 'message',
              'level': 'level', 'code': 'code', 'projectId': 'project_id',
              'workflowId': 'workflow_id', 'workflowRunId': 'workflow_run_id',
              'parentWorkflowRunId': 'parent_workflow_run_id', 'nodeId': 'node_id',
              'nodeRunId': 'node_run_id', 'sequence': 'sequence', 'timestampMs': 'timestamp_ms',
              'originApp': 'origin_app', 'component': 'component', 'runtimeInstanceId': 'runtime_instance_id'}
    result = {name: getattr(event, wire, '') for name, wire in fields.items()}
    result['payload'] = json.loads(event.payload_json or '{}')
    if event.iteration_path_json:
        result['iterationPath'] = json.loads(event.iteration_path_json)
    return result


def logQueryRequest(*, runtimeInstanceId="", projectId="", jobId="", deviceId="",
                    component="", levels=(), workflowId="", nodeId="", startTimestampMs=None,
                    endTimestampMs=None, afterEventId=0, upperEventId=None, limit=200, latestFirst=False,
                    beforeEventId=None):
    request = pb.QueryRuntimeLogsRequest(runtime_instance_id=runtimeInstanceId,
        project_id=projectId, job_id=jobId, device_id=deviceId, component=component,
        levels=levels, workflow_id=workflowId, node_id=nodeId, after_event_id=afterEventId,
        limit=limit, latest_first=latestFirst)
    for name, value in (("upper_event_id", upperEventId), ("start_timestamp_ms", startTimestampMs),
                        ("end_timestamp_ms", endTimestampMs), ("before_event_id", beforeEventId)):
        if value is not None:
            setattr(request, name, value)
    return request


def logQueryPage(reply):
    return dict(ok=reply.ok, code=reply.code, message=reply.message,
        runtimeInstanceId=reply.runtime_instance_id, upperEventId=reply.upper_event_id,
        nextEventId=reply.next_event_id, hasMore=reply.has_more,
        events=[dict(eventId=row.event_id, event=eventPayload(row.event)) for row in reply.events])


def unsupportedLogQuery(afterEventId):
    return dict(ok=False, code="UNIMPLEMENTED", message="此 Runtime 不支持历史日志查询",
                runtimeInstanceId="", upperEventId=0, nextEventId=afterEventId, hasMore=False, events=[])


def nextLogPage(reply):
    return dict(afterEventId=reply["nextEventId"], upperEventId=reply["upperEventId"],
                runtimeInstanceId=reply["runtimeInstanceId"])


def querySystemLogPage(client, options, cancellation, *, cancelled=lambda: False):
    while True:
        if cancelled():
            raise LogExportCancelled("日志任务已取消")
        reply = client.queryRuntimeLogs(**options, cancellation=cancellation)
        if not reply["ok"]:
            return reply
        rows = [row for row in reply["events"] if StructuredLogEntry.fromRuntimeEvent(row["event"]).isRuntimeSystem]
        if rows or not reply["hasMore"]:
            return dict(reply, events=rows)
        options = {**options, **nextLogPage(reply)}


def exportWholeJob(path, formatName, client, scope, cancellation, *, cancelled):
    def page(options):
        if cancelled():
            raise LogExportCancelled("日志导出已取消")
        reply = client.queryRuntimeLogs(**options, cancellation=cancellation)
        if not reply["ok"] or reply["runtimeInstanceId"] != scope["runtimeInstanceId"]:
            raise ValueError(reply["message"] or "Runtime 实例已切换。")
        return reply

    first = page(scope)
    upper = first["upperEventId"]

    def records():
        reply = first
        while True:
            for row in reply["events"]:
                entry = StructuredLogEntry.fromRuntimeEvent(dict(row["event"], eventId=row["eventId"]))
                if entry.effectiveJobId == scope["jobId"] and entry.projectId == scope["projectId"]:
                    yield entry.rawRecord()
            if not reply["hasMore"]:
                return
            reply = page({**scope, "afterEventId": reply["nextEventId"], "upperEventId": upper})

    return exportLogs(path, records(), formatName=formatName, scope="whole_job",
                      filters=dict(scope, upperEventId=upper), cancelled=cancelled)
