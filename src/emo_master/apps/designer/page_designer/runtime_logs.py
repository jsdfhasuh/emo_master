"""Read-only presentation logs for Jobs not owned by the Designer Run command."""
import json
import queue
import threading

import grpc

from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2_grpc as rpc
from emo_master.clients.runtime.connection import connectionProfile, createChannel


def eventPayload(event):
    fields = {'jobId': 'job_id', 'eventType': 'event_type', 'message': 'message',
              'level': 'level', 'code': 'code', 'projectId': 'project_id',
              'workflowId': 'workflow_id', 'workflowRunId': 'workflow_run_id',
              'parentWorkflowRunId': 'parent_workflow_run_id', 'nodeId': 'node_id',
              'nodeRunId': 'node_run_id', 'sequence': 'sequence', 'timestampMs': 'timestamp_ms'}
    result = {name: getattr(event, wire) for name, wire in fields.items()}
    result['payload'] = json.loads(event.payload_json or '{}')
    result['iterationPath'] = json.loads(event.iteration_path_json or '[]')
    return result


class PreviewRuntimeLogs:
    """At most the selected producer/consumer pair; no extra Runtime or Job start."""
    def __init__(self, address, jobIds, afterSequences=None):
        self.connectionProfile = connectionProfile(address)
        self.channel = createChannel(self.connectionProfile)
        self.stub = rpc.RuntimeServiceStub(self.channel)
        self.events = queue.Queue(maxsize=128)
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.streams = {}
        self.cursors = {jobId: int((afterSequences or {}).get(jobId, 0)) for jobId in jobIds}
        self.threads = [threading.Thread(target=self._read, args=(jobId,),
                                        name='page-log-' + jobId[:8])
                        for jobId in dict.fromkeys(jobIds)]
        for thread in self.threads:
            thread.start()

    def _publish(self, kind, value):
        while not self.stop.is_set():
            try:
                self.events.put((kind, value), timeout=.05)
                return True
            except queue.Full:
                continue
        return False

    def _read(self, jobId):
        cursor, reported, failures = self.cursors[jobId], False, 0
        while not self.stop.is_set():
            stream = None
            try:
                stream = self.stub.StreamJobEvents(pb.StreamJobEventsRequest(
                    job_id=jobId, after_sequence=cursor, follow=True))
                with self.lock:
                    if self.stop.is_set():
                        stream.cancel()
                        return
                    self.streams[jobId] = stream
                for event in stream:
                    if self.stop.is_set():
                        return
                    failures, reported = 0, False
                    if event.sequence <= cursor:
                        continue
                    if (event.event_type.startswith('presentation.') or event.level in {'WARN', 'WARNING', 'ERROR'}
                            or event.event_type in {'job.completed', 'job.failed', 'job.aborted'}):
                        if not self._publish('event', eventPayload(event)):
                            return
                    cursor = event.sequence
                    with self.lock:
                        self.cursors[jobId] = cursor
                return
            except grpc.RpcError as error:
                if self.stop.is_set():
                    return
                failures += 1
                detail = f'页面日志读取失败（任务 {jobId}）：{error.code().name}'
                if error.code() not in {grpc.StatusCode.UNAVAILABLE, grpc.StatusCode.DEADLINE_EXCEEDED} or failures > 3:
                    self._publish('error', detail + '；日志订阅已停止，请重新查看运行结果以重连')
                    return
                if not reported:
                    self._publish('error', detail + '；正在重连，最多重试 3 次')
                    reported = True
                if self.stop.wait(.5):
                    return
            except Exception as error:
                if not self.stop.is_set():
                    self._publish('error', f'页面日志读取失败（任务 {jobId}）：{type(error).__name__}: {error}')
                return
            finally:
                if stream is not None:
                    stream.cancel()
                with self.lock:
                    self.streams.pop(jobId, None)

    def take(self):
        rows = []
        for _ in range(128):
            try:
                rows.append(self.events.get_nowait())
            except queue.Empty:
                break
        return rows

    def close(self):
        self.stop.set()
        with self.lock:
            for stream in self.streams.values():
                stream.cancel()
        self.channel.close()
        for thread in self.threads:
            thread.join()

    def positions(self):
        with self.lock:
            return dict(self.cursors)
