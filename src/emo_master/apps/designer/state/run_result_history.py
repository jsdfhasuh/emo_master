"""Two explicit runs, independent of Qt, mutable drafts and the live canvas."""
from collections import OrderedDict
from dataclasses import dataclass, field
from copy import deepcopy
import time

from emo_master.core.contracts.run_inspection import clipText, encodedBytes
from .node_run_inspection import NodeRunInspection

RUN_METADATA_LIMIT = 512 * 1024


@dataclass
class RunResultRecord:
    jobId: str
    revision: int
    acceptedAtMs: int
    definitions: dict = field(default_factory=dict)
    inspection: NodeRunInspection = field(default_factory=NodeRunInspection)
    status: str = 'ACCEPTED'
    artifact: dict = field(default_factory=dict)
    notice: str = ''

    @property
    def retainedBytes(self):
        return self.inspection.retainedBytes + encodedBytes({
            'jobId': self.jobId, 'revision': self.revision, 'acceptedAtMs': self.acceptedAtMs,
            'definitions': list(self.definitions.values()), 'status': self.status,
            'artifact': self.artifact, 'notice': self.notice})

    def enforceLimit(self):
        while self.inspection._nodes and self.retainedBytes > RUN_METADATA_LIMIT:
            self.inspection._nodes.popitem(last=False)
            self.notice = 'E_INSPECTION_METADATA_BUDGET: 节点摘要超过会话额度，最早更新的记录已释放'


class RunResultHistory:
    def __init__(self):
        self.runs: OrderedDict[str, RunResultRecord] = OrderedDict()
        self.viewJobId: str | None = None
        self.notice = ''

    @property
    def current(self):
        return next(reversed(self.runs.values()), None)

    @property
    def previous(self):
        return list(self.runs.values())[-2] if len(self.runs) == 2 else None

    @property
    def selected(self):
        return self.runs.get(self.viewJobId or '')

    @property
    def retainedBytes(self):
        return sum(run.retainedBytes for run in self.runs.values())

    def clear(self):
        self.runs.clear()
        self.viewJobId = None
        self.notice = ''

    def accept(self, jobId, definitions=(), revision=1, acceptedAtMs=0):
        if not isinstance(jobId, str) or not jobId or len(jobId.encode()) > 128:
            return
        if jobId in self.runs:
            return
        wasLive = self.selected is self.current
        frozen = {}
        limited = False
        for node in definitions:
            if len(frozen) >= 64 or not isinstance(node, dict):
                limited = True
                break
            identity = (str(node.get('workflowId', '')), str(node.get('nodeId', '')))
            definition = {name: clipText(str(node.get(name, '')), 128)
                          for name in ('workflowId', 'nodeId', 'displayName', 'operatorId')}
            name = str(node.get('displayName', ''))
            definition['displayName'] = clipText(name, 2048)
            definition['displayNameTruncated'] = definition['displayName'] != name
            for side in ('inputPorts', 'outputPorts'):
                ports = node.get(side, {})
                limited = limited or (isinstance(ports, dict) and len(ports) > 64)
                definition[side] = {clipText(str(port), 64): clipText(str(kind), 64)
                                    for port, kind in list(ports.items())[:64]} if isinstance(ports, dict) else {}
            if encodedBytes(definition) <= 4096:
                frozen[identity] = definition
            else:
                limited = True
        record = RunResultRecord(jobId, int(revision), int(acceptedAtMs or time.time() * 1000), frozen)
        if limited:
            record.notice = 'E_INSPECTION_METADATA_BUDGET: 节点定义或端口超过摘要额度，部分内容未保留（节点上限64）'
        self.runs[jobId] = record
        self.notice = ''
        while len(self.runs) > 2:
            evicted, _ = self.runs.popitem(last=False)
            if self.viewJobId == evicted:
                self.notice = '正在查看的任务已超出两次运行的保留范围，已返回本次运行'
        if wasLive or self.viewJobId not in self.runs:
            self.viewJobId = jobId

    def selectPrevious(self, previous):
        run = self.previous if previous else self.current
        if run:
            self.viewJobId = run.jobId
            self.notice = ''

    def applyEvent(self, event):
        run = self.runs.get(event.get('jobId', ''))
        if run is None:
            return False
        # A run's final status is immutable; late invocation deliveries must not
        # resurrect a result after its terminal event.
        kind = event.get('eventType', '')
        if run.status in {'COMPLETED', 'FAILED', 'ABORTED'} and kind.startswith('node.'):
            return False
        identifiers = (event.get('workflowId', ''), event.get('nodeId', ''))
        willEvict = (kind in {'node.started', 'node.completed', 'node.failed', 'node.skipped'}
                     and all(isinstance(value, str) for value in identifiers)
                     and identifiers not in run.inspection._nodes and len(run.inspection._nodes) >= 64)
        if not run.inspection.applyEvent(event):
            return False
        if willEvict:
            run.notice = 'E_INSPECTION_METADATA_BUDGET: 节点摘要超过64条，最早更新的记录已释放；可查看任务日志'
        if kind in {'job.completed', 'job.failed', 'job.aborted'}:
            run.status = kind.split('.')[1].upper()
        elif kind == 'node.started':
            run.status = 'RUNNING'
        elif kind == 'artifact.created':
            artifact = event.get('payload', {}).get('artifact', {})
            if isinstance(artifact, dict):
                run.artifact = {key: clipText(str(artifact.get(key, '')), 512)
                                for key in ('path', 'nodeId', 'artifactId', 'mimeType', 'checksum')}
                run.artifact.update(workflowId=event.get('workflowId', ''), nodeId=event.get('nodeId', ''),
                                    nodeRunId=event.get('nodeRunId', ''), workflowRunId=event.get('workflowRunId', ''))
        run.enforceLimit()
        return True

    def view(self, workflowId, nodeId):
        run = self.selected
        if run is None:
            return {'job': None, 'definition': None, 'record': None, 'history': False}
        return {'job': run, 'definition': deepcopy(run.definitions.get((workflowId, nodeId))),
                'record': deepcopy(run.inspection.get(workflowId, nodeId, run.jobId)),
                'history': run is not self.current}
