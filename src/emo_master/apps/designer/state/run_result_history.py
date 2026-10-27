"""The last two actual invocations of each node, independent of the canvas."""
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
    _definitionBytes: int = field(init=False, repr=False)

    def __post_init__(self):
        self.setDefinitions(self.definitions)

    def setDefinitions(self, definitions):
        self.definitions = definitions
        self._definitionBytes = encodedBytes(list(definitions.values())) - 2

    @property
    def retainedBytes(self):
        return self.inspection.retainedBytes + self._definitionBytes + encodedBytes({
            'jobId': self.jobId, 'revision': self.revision, 'acceptedAtMs': self.acceptedAtMs,
            'definitions': [], 'status': self.status,
            'artifact': self.artifact, 'notice': self.notice})

    def enforceLimit(self):
        while self.inspection._nodes and self.retainedBytes > RUN_METADATA_LIMIT:
            self.inspection.discardOldest()
            self.notice = 'E_INSPECTION_METADATA_BUDGET: 节点摘要超过会话额度，最早更新的记录已释放'


class RunResultHistory:
    def __init__(self):
        self.runs: OrderedDict[str, RunResultRecord] = OrderedDict()
        self.viewJobId: str | None = None
        self.notice = ''
        self.executions = OrderedDict()
        self._executionBytes = {}
        self.nodeIdentity = None
        self.older = False
        self.contextId = None

    @property
    def current(self):
        return next(reversed(self.runs.values()), None)

    @property
    def previous(self):
        entries = self.executions.get(self.nodeIdentity, ())
        return entries[-2]['run'] if len(entries) == 2 else None

    @property
    def selected(self):
        entries = self.executions.get(self.nodeIdentity, ())
        if entries:
            return entries[-2 if self.older and len(entries) == 2 else -1]['run']
        return self.current

    @property
    def retainedBytes(self):
        # Each node owns at most two frozen records. Re-encode only that node
        # when it changes, not every retained invocation on every event.
        executions = sum(self._executionBytes.values()) + len(self.executions) + 1 if self.executions else 0
        return sum(run.retainedBytes for run in self.runs.values()) + executions

    def _measureExecution(self, key):
        self._executionBytes[key] = encodedBytes([
            {name: value for name, value in entry.items() if name != 'run'}
            for entry in self.executions[key]])

    def clear(self):
        self.runs.clear()
        self.viewJobId = None
        self.notice = ''
        self.executions.clear()
        self._executionBytes.clear()
        self.nodeIdentity = None
        self.older = False
        self.contextId = None

    def bindContext(self, contextId):
        if self.contextId is not None and self.contextId != contextId:
            self.clear()
        self.contextId = contextId

    def accept(self, jobId, definitions=(), revision=1, acceptedAtMs=0):
        if not isinstance(jobId, str) or not jobId or len(jobId.encode()) > 128:
            return
        if jobId in self.runs:
            return
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
        self.viewJobId = jobId
        self._pruneJobs()

    def selectPrevious(self, previous):
        self.older = bool(previous and self.previous)
        self.notice = ''

    def _pruneJobs(self):
        retained = {entry['run'].jobId for entries in self.executions.values() for entry in entries}
        if self.current:
            retained.add(self.current.jobId)
        for jobId in list(self.runs):
            if jobId not in retained:
                del self.runs[jobId]
            elif self.runs[jobId] is not self.current:
                run = self.runs[jobId]
                nodes = {key for key, entries in self.executions.items()
                         if any(entry['run'] is run for entry in entries)}
                if any(key not in nodes for key in run.definitions):
                    run.setDefinitions({key: value for key, value in run.definitions.items() if key in nodes})
                run.inspection.retainNodes(nodes)

    def setStatus(self, jobId, status):
        run = self.runs.get(jobId)
        if run is None:
            return
        run.status = status
        if status not in {'COMPLETED', 'FAILED', 'ABORTED'}:
            return
        for key, entries in self.executions.items():
            changed = False
            for entry in entries:
                record = entry['record']
                if record['jobId'] == jobId and record['status'] == 'RUNNING':
                    record['status'] = 'CANCELLED' if status == 'ABORTED' else 'INTERRUPTED'
                    record['message'] = '任务已结束，本次节点执行未完成'
                    changed = True
            if changed:
                self._measureExecution(key)

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
            self.setStatus(run.jobId, kind.split('.')[1].upper())
        elif kind == 'node.started':
            run.status = 'RUNNING'
        elif kind == 'artifact.created':
            artifact = event.get('payload', {}).get('artifact', {})
            if isinstance(artifact, dict):
                run.artifact = {key: clipText(str(artifact.get(key, '')), 512)
                                for key in ('path', 'nodeId', 'artifactId', 'mimeType', 'checksum')}
                run.artifact.update(workflowId=event.get('workflowId', ''), nodeId=event.get('nodeId', ''),
                                    nodeRunId=event.get('nodeRunId', ''), workflowRunId=event.get('workflowRunId', ''))
        if kind in {'node.started', 'node.completed', 'node.failed'}:
            record = run.inspection.get(*identifiers, run.jobId)
            if record:
                if record['status'] == 'FAILED' and record['code'] == 'E_CANCELLED':
                    record = {**record, 'status': 'CANCELLED'}
                if self.nodeIdentity is None:
                    self.nodeIdentity = identifiers
                entries = self.executions.setdefault(identifiers, [])
                identity = (record['jobId'], record['workflowRunId'], record['nodeRunId'])
                if not entries or identity != tuple(entries[-1]['record'][key] for key in
                                                   ('jobId', 'workflowRunId', 'nodeRunId')):
                    entries.append({'run': run, 'definition': deepcopy(run.definitions.get(identifiers)),
                                    'record': deepcopy(record), 'artifact': {}})
                    del entries[:-2]
                else:
                    entries[-1]['record'] = deepcopy(record)
                artifact = run.artifact
                if all(artifact.get(key) == record[key] for key in ('workflowId', 'nodeId', 'workflowRunId', 'nodeRunId')):
                    entries[-1]['artifact'] = deepcopy(artifact)
                self.executions.move_to_end(identifiers)
                self._measureExecution(identifiers)
                self._pruneJobs()
                while self.executions and (len(self.executions) > 64 or self.retainedBytes > 2 * RUN_METADATA_LIMIT):
                    removed, _ = self.executions.popitem(last=False)
                    self._executionBytes.pop(removed, None)
                    self._pruneJobs()
                    run.notice = 'E_INSPECTION_METADATA_BUDGET: 节点摘要超过额度，最早更新的记录已释放'
        run.enforceLimit()
        return True

    def view(self, workflowId, nodeId):
        self.nodeIdentity = (workflowId, nodeId)
        entries = self.executions.get(self.nodeIdentity, ())
        older = self.older and len(entries) == 2
        entry = entries[-2 if older else -1] if entries else None
        run = entry['run'] if entry else self.current
        if run is None:
            return {'job': None, 'definition': None, 'record': None, 'history': False,
                    'hasPrevious': False, 'artifact': {}, 'observation': None, 'currentJobId': ''}
        self.viewJobId = run.jobId
        return {'job': run, 'definition': deepcopy(entry['definition'] if entry else run.definitions.get(self.nodeIdentity)),
                'record': deepcopy(entry['record']) if entry else None, 'history': older,
                'hasPrevious': len(entries) == 2, 'artifact': deepcopy(entry['artifact']) if entry else {},
                'observation': self.current.inspection.get(workflowId, nodeId, self.current.jobId),
                'currentJobId': self.current.jobId}
