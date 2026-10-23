"""Leased, read-only ownership of each node's last two actual invocations.

Event sinks update metadata only. Immutable preview files arrive on the
existing worker channel; copies and retirement run outside its order lock.
"""
from collections import OrderedDict
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import shutil
import threading
import time
from uuid import uuid4

from emo_master.core.contracts.port_types import normalizePortType
from . import store as legacy
from .store import PreviewAsset

JOB_BYTES = 64 * 1024 * 1024
SESSION_BYTES = 2 * JOB_BYTES
TTL_SECONDS = 30
METADATA_BYTES = 512 * 1024


@dataclass
class InspectionExecution:
    jobId: str
    workflowId: str
    nodeId: str
    workflowRunId: str
    nodeRunId: str
    status: str = 'node.started'
    sources: list = field(default_factory=list)
    artifact: dict = field(default_factory=dict)
    reason: str = ''
    readers: int = 0
    directory: Path | None = None


@dataclass
class InspectionJob:
    jobId: str
    revision: int
    definitions: dict
    last: dict = field(default_factory=dict)
    ready: bool = False
    status: str = 'ACCEPTED'


@dataclass
class InspectionSession:
    sessionId: str
    projectKey: str
    expiresAt: float
    jobs: OrderedDict = field(default_factory=OrderedDict)
    retired: list = field(default_factory=list)
    closed: bool = False
    pending: dict = field(default_factory=dict)
    projectReferences: frozenset = field(default_factory=frozenset)
    executions: OrderedDict = field(default_factory=OrderedDict)


class RunInspectionStore:
    def __init__(self, assets, *, clock=time.monotonic, startMaintenance=True, maxSessions=2):
        self.assets = assets
        self.root = assets.root / '_inspection'
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = assets._lock
        self._eventLock = threading.RLock()
        self._watches = {}
        self.clock = clock
        self.sessions: dict[str, InspectionSession] = {}
        self.maxSessions = maxSessions
        self._pendingFiles: set[Path] = set()
        self._readers = 0
        self._stop = threading.Event()
        for path in self.root.iterdir():
            if path.is_dir():
                try:
                    shutil.rmtree(path)
                except OSError:
                    self._pendingFiles.update(p for p in path.rglob('*') if p.is_file())
        assets.inspectionUsage = self.usage
        self.thread = threading.Thread(target=self._maintenance, name='node-inspection-leases', daemon=True)
        if startMaintenance:
            self.thread.start()

    def _maintenance(self):
        while not self._stop.wait(1):
            self.expire()

    def usage(self, projectKey=None):
        with self._lock:
            root = self.root / projectKey if projectKey else self.root
            return sum(path.stat().st_size for path in root.rglob('*') if path.is_file()) if root.exists() else 0

    def _publicUsage(self, projectKey=None):
        if projectKey:
            root = self.assets.root / projectKey
            total = sum(p.stat().st_size for p in root.rglob('*') if p.is_file()) if root.exists() else 0
            total += sum(a.path.stat().st_size for a in self.assets._assets.values()
                         if a.path.parent == self.assets.transientRoot and a.projectKey == projectKey and a.path.exists())
            return total + self.usage(projectKey)
        return sum(p.stat().st_size for p in self.assets.root.rglob('*') if p.is_file())

    def open(self, projectKey, projectReferences=()):
        with self._lock:
            self.expire()
            if self._stop.is_set() or (self.maxSessions is not None and len(self.sessions) >= self.maxSessions):
                raise ValueError('E_INSPECTION_SESSION_BUDGET: inspection session admission unavailable')
            session = InspectionSession(str(uuid4()), projectKey, self.clock() + TTL_SECONDS,
                                        projectReferences=frozenset(projectReferences))
            self.sessions[session.sessionId] = session
            return session.sessionId

    def keyForReference(self, sessionId, projectReference):
        with self._lock:
            self.expire()
            session = self.sessions.get(sessionId)
            if session is None or session.closed or projectReference not in session.projectReferences:
                raise ValueError('E_INSPECTION_SESSION_EXPIRED: session expired or belongs to another project copy')
            return session.projectKey

    def require(self, sessionId, projectKey):
        with self._lock:
            self.expire()
            session = self.sessions.get(sessionId)
            if session is None or session.closed or session.projectKey != projectKey:
                raise ValueError('E_INSPECTION_SESSION_EXPIRED: session expired or belongs to another project copy')
            return session

    def renew(self, sessionId, projectKey):
        with self._lock:
            self.require(sessionId, projectKey).expiresAt = self.clock() + TTL_SECONDS

    def attach(self, sessionId, projectKey, jobId, document, *, accepted=True):
        with self._lock:
            session = self.require(sessionId, projectKey)
            definitions = {}
            nodes = sorted(((workflowId, node) for workflowId, workflow in document.workflows.items()
                            for node in workflow.nodes), key=lambda item: (item[0], item[1].nodeId))
            for workflowId, node in nodes[:64]:
                if any(len(str(value).encode()) > 128 for value in (workflowId, node.nodeId)):
                    continue
                ports = dict(list(node.outputPorts.items())[:64])
                if len(json.dumps(ports).encode()) <= 4096 and all(len(str(port).encode()) <= 64 for port in ports):
                    definitions[(workflowId, node.nodeId)] = ports
            job = InspectionJob(jobId, document.project.revision, definitions)
            if session.pending:
                raise ValueError('E_INSPECTION_PENDING: another start is not yet resolved')
            with self._eventLock:
                session.pending[jobId] = job
                self._watches[jobId] = (session, job)
            if accepted:
                self.confirm(sessionId, projectKey, jobId)

    def confirm(self, sessionId, projectKey, jobId):
        with self._lock:
            session = self.require(sessionId, projectKey)
            with self._eventLock:
                session.jobs[jobId] = session.pending.pop(jobId)
                self._pruneJobs(session)
            self._cleanup(session)

    def _pruneJobs(self, session):
        retained = {execution.jobId for entries in session.executions.values() for execution in entries}
        retained.update(list(session.jobs)[-1:])
        for jobId, job in list(session.jobs.items()):
            if jobId not in retained and job.ready:
                del session.jobs[jobId]
                self._watches.pop(jobId, None)
            elif job.ready:
                nodes = {key for key, entries in session.executions.items()
                         if any(execution.jobId == jobId for execution in entries)}
                job.definitions = {key: value for key, value in job.definitions.items() if key in nodes}
                job.last = {key: value for key, value in job.last.items() if key in nodes}

    def abandon(self, sessionId, projectKey, jobId):
        with self._lock:
            session = self.sessions.get(sessionId)
            if session is None or session.projectKey != projectKey:
                return
            with self._eventLock:
                session.pending.pop(jobId, None)
                self._watches.pop(jobId, None)

    def observe(self, event):
        """The EventStore sink does no encoding, copying, deletion or disk IO."""
        with self._eventLock:
            watched = self._watches.get(event.jobId)
            if watched is None:
                return
            session, job = watched
            key = (event.workflowId, event.nodeId)
            if key not in job.definitions or job.ready:
                return
            entries = session.executions.get(key, [])
            execution = next((item for item in reversed(entries)
                              if (item.jobId, item.workflowRunId, item.nodeRunId) ==
                              (event.jobId, event.workflowRunId, event.nodeRunId)), None)
            if event.eventType == 'artifact.created' and execution and len(event.payloadJson.encode()) <= 16384:
                artifact = json.loads(event.payloadJson).get('artifact', {})
                if isinstance(artifact, dict) and str(artifact.get('mimeType', '')).startswith('image/'):
                    execution.artifact = {name: str(artifact.get(name, ''))[:1024]
                                          for name in ('path', 'checksum', 'artifactId', 'mimeType')}
                return
            if event.eventType not in ('node.started', 'node.completed', 'node.failed', 'node.skipped'):
                return
            previous = job.last.get(key)
            if previous and (event.sequence <= previous['sequence'] or
                    (event.eventType in ('node.completed', 'node.failed') and
                     (previous['workflowRunId'], previous['nodeRunId']) != (event.workflowRunId, event.nodeRunId))):
                return
            job.last[key] = dict(nodeRunId=event.nodeRunId, workflowRunId=event.workflowRunId,
                                 status=event.eventType, sequence=event.sequence)
            if event.eventType == 'node.skipped':
                return
            if execution is None:
                execution = InspectionExecution(event.jobId, event.workflowId, event.nodeId,
                                                 event.workflowRunId, event.nodeRunId)
                directoryId = hashlib.sha256(('\0'.join((event.workflowId, event.nodeId,
                    event.workflowRunId, event.nodeRunId))).encode()).hexdigest()
                execution.directory = self.root / session.projectKey / session.sessionId / event.jobId / directoryId
                entries.append(execution)
                session.retired.extend(entries[:-2])
                del entries[:-2]
                session.executions[key] = entries
                session.executions.move_to_end(key)
                while len(session.executions) > 64:
                    session.retired.extend(session.executions.popitem(last=False)[1])
            execution.status = 'node.cancelled' if event.eventType == 'node.failed' and event.code == 'E_CANCELLED' else event.eventType
            self._pruneJobs(session)

    def publish(self, jobId, event, workspace):
        """Accept immutable worker previews before the following node.completed."""
        staging = legacy._ioPath(workspace / 'inspection_staging').resolve()
        entries = event.get('payload', {}).get('entries', ())
        for entry in entries:
            source = (staging / str(entry.get('relativePath', ''))).resolve()
            if not source.is_relative_to(staging) or not source.is_file():
                continue
            try:
                with self._lock:
                    with self._eventLock:
                        watched = self._watches.get(jobId)
                        if watched is None:
                            continue
                        session, job = watched
                        key = (entry.get('workflowId'), entry.get('nodeId'))
                        execution = next((item for item in session.executions.get(key, ())
                            if (item.jobId, item.workflowRunId, item.nodeRunId) ==
                            (jobId, entry.get('workflowRunId'), entry.get('nodeRunId'))), None)
                        if execution is None:
                            continue
                        if (entry.get('originJobId') != jobId
                                or entry.get('originProjectRevision') != job.revision or not entry.get('captureId')):
                            execution.reason = 'E_INSPECTION_IDENTITY: 快照与所选执行身份不匹配'
                            continue
                        port = entry.get('port')
                        if port == '__saved_result__':
                            if (execution.artifact.get('artifactId') != entry['captureId']
                                    or not execution.artifact.get('checksum')
                                    or not Path(execution.artifact['path']).resolve().is_relative_to(
                                        (workspace / 'artifacts').resolve())):
                                continue
                        elif normalizePortType(job.definitions.get(key, {}).get(port, '')) != 'image':
                            continue
                    self._cleanup(session)
                    self._copy(session, job, execution, entry, source)
            finally:
                source.unlink(missing_ok=True)

    def _copy(self, session, job, execution, entry, source):
        size = source.stat().st_size
        sessionRoot = self.root / session.projectKey / session.sessionId
        sessionUsage = sum(path.stat().st_size for path in sessionRoot.rglob('*') if path.is_file())
        with self._eventLock:
            sources = [asset for entries in session.executions.values() for item in entries for asset in item.sources]
        jobRoot = sessionRoot / job.jobId
        jobUsage = sum(path.stat().st_size for path in jobRoot.rglob('*') if path.is_file())
        if (size > JOB_BYTES - jobUsage or sessionUsage + size > SESSION_BYTES
                or self._publicUsage(session.projectKey) + size > legacy._PROJECT_LIMIT_BYTES
                or self._publicUsage() + size > legacy._GLOBAL_LIMIT_BYTES or self._pendingFiles
                or any(old.directory and old.directory.exists() and not old.readers for old in session.retired)):
            execution.reason = 'E_INSPECTION_ASSET_BUDGET: 图片未接管，存储额度不足或清理未完成'
            return
        if len(sources) >= 256:
            execution.reason = 'E_INSPECTION_METADATA_BUDGET: 图片端口元数据额度不足'
            return
        identity = hashlib.sha256(('\0'.join((session.sessionId, job.jobId, execution.workflowId,
            execution.nodeId, execution.workflowRunId, execution.nodeRunId, entry['captureId'], entry['port']))).encode()).hexdigest()
        assetId = 'inspection-' + identity
        if any(asset.assetId == assetId for asset in execution.sources):
            return
        destination = execution.directory / (assetId + '.png')
        asset = PreviewAsset(assetId=assetId, path=destination, mimeType='image/png',
            width=int(entry['width']), height=int(entry['height']), projectKey=session.projectKey,
            workflowId=execution.workflowId, nodeId=execution.nodeId, port=entry['port'], portType='image',
            iterationPath=tuple(entry.get('iterationPath', ())), createdAtMs=int(entry.get('createdAtMs', 0)),
            originJobId=job.jobId, originProjectRevision=job.revision, captureId=entry['captureId'],
            workflowRunId=execution.workflowRunId, nodeRunId=execution.nodeRunId,
            originalWidth=int(entry.get('originalWidth', entry['width'])),
            originalHeight=int(entry.get('originalHeight', entry['height'])))
        with self._eventLock:
            if (self.metadataBytes(session, job.jobId, asset) > METADATA_BYTES
                    or self.metadataBytes(session, additional=asset) > 2 * METADATA_BYTES):
                execution.reason = 'E_INSPECTION_METADATA_BUDGET: 节点图片元数据超过会话额度'
                return
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            legacy._atomicCopy(source, destination)
        except OSError as error:
            execution.reason = 'E_INSPECTION_TAKEOVER: 节点图片接管失败：' + str(error)[:200]
            self._pendingFiles.update(destination.parent.glob('.*.tmp'))
            return
        with self._eventLock:
            execution.sources.append(asset)

    @staticmethod
    def metadataBytes(session, jobId=None, additional=None):
        jobs = [{'jobId': job.jobId, 'revision': job.revision, 'definitions': list(job.definitions.items()),
                 'last': list(job.last.items()), 'status': job.status}
                for job in session.jobs.values() if jobId is None or job.jobId == jobId]
        executions = [{**execution.__dict__, 'sources': [asset.__dict__ for asset in execution.sources]}
                      for entries in session.executions.values() for execution in entries
                      if jobId is None or execution.jobId == jobId]
        value = {'jobs': jobs, 'executions': executions, 'additional': additional.__dict__ if additional else None}
        return len(json.dumps(value, default=str, separators=(',', ':')).encode())

    def terminal(self, jobId, workspace, status):
        with self._lock:
            with self._eventLock:
                watched = self._watches.pop(jobId, None)
                if watched is None:
                    return
                session, job = watched
                job.status, job.ready = status, True
                for entries in session.executions.values():
                    for execution in entries:
                        if execution.jobId == jobId and execution.status == 'node.started':
                            execution.status = 'node.cancelled' if status == 'ABORTED' else 'node.interrupted'
                self._pruneJobs(session)
            self._cleanup(session)

    def list(self, sessionId, projectKey, jobId, workflowId, nodeId, workflowRunId='', nodeRunId=''):
        with self._lock:
            session = self.require(sessionId, projectKey)
            self._cleanup(session)
            with self._eventLock:
                job = session.jobs.get(jobId)
                if job is None:
                    return [], 'EXPIRED', '任务已超出节点执行保留范围'
                entries = session.executions.get((workflowId, nodeId), ())
                execution = next((item for item in reversed(entries) if item.jobId == jobId
                    and (not workflowRunId or item.workflowRunId == workflowRunId)
                    and (not nodeRunId or item.nodeRunId == nodeRunId)), None)
                if execution is None:
                    return [], 'NO_CURRENT_SNAPSHOT', 'E_INSPECTION_MISSING: 所选节点执行未采集或已淘汰'
                if execution.status == 'node.started':
                    return [], 'PREPARING', '节点正在执行，图片尚未发布'
                if execution.status != 'node.completed':
                    return [], 'NO_CURRENT_SNAPSHOT', 'E_INSPECTION_NOT_COMPLETED: 此次执行没有有效完成输出'
                sources = list(execution.sources)
                return sources, 'CURRENT_AVAILABLE' if sources else 'NO_CURRENT_SNAPSHOT', \
                    execution.reason or ('' if sources else 'E_INSPECTION_MISSING: 本次未输出或未采集此端口')

    def stream(self, sessionId, projectKey, assetId, active=lambda: True):
        with self._lock:
            session = self.require(sessionId, projectKey)
            with self._eventLock:
                match = next(((execution, asset) for entries in session.executions.values() for execution in entries
                    for asset in execution.sources if asset.assetId == assetId and execution.status == 'node.completed'), None)
                if match is None:
                    raise KeyError('inspection asset is not retained by this session')
                if self._readers >= 2:
                    raise ValueError('E_INSPECTION_READ_BUDGET: at most two actual readers')
                execution, asset = match
                execution.readers += 1
                self._readers += 1
        try:
            with asset.path.open('rb') as handle:
                while active():
                    chunk = handle.read(256 * 1024)
                    if not chunk:
                        return
                    yield chunk, asset.mimeType
        finally:
            with self._lock:
                execution.readers -= 1
                self._readers -= 1
                self._cleanup(session)

    def closeSession(self, sessionId, projectKey=None):
        with self._lock:
            session = self.sessions.get(sessionId)
            if session is None:
                return
            if projectKey is not None and session.projectKey != projectKey:
                raise ValueError('inspection session belongs to another project')
            with self._eventLock:
                session.closed = True
                for job in (*session.jobs.values(), *session.pending.values()):
                    self._watches.pop(job.jobId, None)
                session.retired.extend(item for entries in session.executions.values() for item in entries)
                session.executions.clear()
                session.jobs.clear()
                session.pending.clear()
            self._cleanup(session)

    def _cleanup(self, session):
        with self._eventLock:
            retired = list(session.retired)
            for execution in retired:
                execution.sources.clear()
                execution.artifact.clear()
                execution.reason = ''
        for execution in retired:
            if execution.readers:
                continue
            try:
                if execution.directory and execution.directory.exists():
                    shutil.rmtree(execution.directory)
            except OSError:
                continue
            with self._eventLock:
                session.retired.remove(execution)
        if session.closed and not session.retired:
            self.sessions.pop(session.sessionId, None)
            root = self.root / session.projectKey / session.sessionId
            if root.exists():
                try:
                    shutil.rmtree(root)
                except OSError:
                    self._pendingFiles.update(p for p in root.rglob('*') if p.is_file())

    def expire(self):
        with self._lock:
            for session in list(self.sessions.values()):
                if session.closed or self.clock() >= session.expiresAt:
                    self.closeSession(session.sessionId)
                else:
                    self._cleanup(session)
            for path in list(self._pendingFiles):
                try:
                    path.unlink(missing_ok=True)
                    self._pendingFiles.discard(path)
                except OSError:
                    pass

    def stats(self):
        with self._lock:
            return {'sessions': len(self.sessions), 'readers': self._readers, 'encodedBytes': self.usage(),
                    'jobs': sum(len(s.jobs) for s in self.sessions.values()),
                    'retiredJobs': sum(len(s.retired) for s in self.sessions.values()),
                    'pendingFiles': len(self._pendingFiles)}

    def close(self):
        self._stop.set()
        if self.thread.is_alive():
            self.thread.join(2)
        with self._lock:
            for sessionId in list(self.sessions):
                self.closeSession(sessionId)
            self.expire()
            if self.sessions or self._pendingFiles or self._readers or self.thread.is_alive():
                raise RuntimeError('Run inspection still owns readers or files; cleanup incomplete')
