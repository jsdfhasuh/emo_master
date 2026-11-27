"""Read the Runtime already owned by Operator's production controller."""
import json

from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.clients.runtime.calls import DisplayCallContext
from emo_master.clients.runtime.devices import DeviceManagementClient
from emo_master.clients.runtime.log_records import logQueryPage, logQueryRequest, unsupportedLogQuery


class OperatorLogClient:
    def __init__(self, service):
        self.runtimeService = service
        self.devices = DeviceManagementClient(service)
        self.operators = None

    def _read(self, name, request, cancellation=None):
        context = cancellation or DisplayCallContext()
        context.check()
        result = getattr(self.runtimeService, name)(request, context)
        context.check()
        return result

    def queryRuntimeLogs(self, *, cancellation=None, **options):
        if not callable(getattr(self.runtimeService, "QueryRuntimeLogs", None)):
            return unsupportedLogQuery(options.get("afterEventId", 0))
        return logQueryPage(self._read("QueryRuntimeLogs", logQueryRequest(**options), cancellation))

    def getRuntimeInfo(self):
        reply = self._read("GetRuntimeInfo", pb.RuntimeInfoRequest())
        if self.operators is None:
            self._loadOperatorDefinitions()
        return reply

    def listRuns(self, runtimeInstanceId, projectId, *, includeTerminal=False):
        return self._read("ListRuns", pb.ListRunsRequest(runtime_instance_id=runtimeInstanceId,
            project_id=projectId, include_terminal=includeTerminal))

    def listJobHistory(self, runtimeInstanceId, projectId="", *, statuses=(), startEndedAtMs=None,
                       endEndedAtMs=None, limit=50, cursor="", cancellation=None):
        request = pb.ListJobHistoryRequest(runtime_instance_id=runtimeInstanceId, project_id=projectId,
            statuses=statuses, limit=limit, cursor=cursor)
        if startEndedAtMs is not None:
            request.start_ended_at_ms = startEndedAtMs
        if endEndedAtMs is not None:
            request.end_ended_at_ms = endEndedAtMs
        return self._read("ListJobHistory", request, cancellation)

    def getRunState(self, runtimeInstanceId, projectId, jobId):
        return self._read("GetRunState", pb.GetRunStateRequest(runtime_instance_id=runtimeInstanceId,
            project_id=projectId, job_id=jobId))

    def getJobStatus(self, jobId):
        return self._read("GetJobStatus", pb.GetJobStatusRequest(job_id=jobId))

    def getDeviceDiagnostics(self, scope, deviceId, cursor=0, *, cancellation=None):
        return self.devices.getDeviceDiagnostics(scope, deviceId, cursor, cancellation=cancellation)

    def _loadOperatorDefinitions(self):
        def schema(value):
            try:
                record = json.loads(value)
            except ValueError:
                return {}
            return record if isinstance(record, dict) else {}

        reply = self._read("ListOperators", pb.ListOperatorsRequest())
        self.operators = {(row.operator_id, row.version): dict(operatorId=row.operator_id,
            version=row.version, paramSchema=schema(row.param_schema_json), resultSchema=schema(row.result_schema_json))
            for row in reply.operators}

    def operatorLookup(self, operatorId, version):
        return (self.operators or {}).get((operatorId, version), {})

    def close(self):
        self.devices.close()
