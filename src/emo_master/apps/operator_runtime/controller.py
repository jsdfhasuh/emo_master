"""Single Runtime owner for explicitly selected existing workflows. No Qt imports."""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import wraps
from pathlib import Path
import time
from uuid import uuid4
import threading

from emo_master.apps.runtime.grpc_server.aio_entry import AioRuntimeServer
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from emo_master.apps.runtime.presentation.service import PresentationService
from emo_master.core.project.models import ProductionSettings
from emo_master.core.project.delivery_store import DirectoryOwner
from emo_master.core.presentation.workflow_view import presentationForWorkflow
from emo_master.apps.operator_runtime.settings import resolveDataRoot


@dataclass
class WorkflowSession:
    jobId: str = ""
    requestId: str = ""
    startUncertain: bool = False


def serialized(method):
    @wraps(method)
    def call(self, *args, **kwargs):
        with self.operationLock:
            return method(self, *args, **kwargs)
    return call


def defaultDataRoot() -> Path:
    # Non-GUI callers retain environment/system defaults; GUI passes its resolved preference.
    return resolveDataRoot().path


class RuntimeInitializationError(RuntimeError):
    def __init__(self, path, stage, cause):
        self.path, self.stage, self.cause = Path(path), stage, cause
        super().__init__(f"{stage}\n目标：{path}\n原因：{cause}")


class ProductionRuntime:
    def __init__(self, dataRoot: Path | None = None, *, pluginRootPaths=None):
        self.dataRoot = (dataRoot or defaultDataRoot()).resolve()
        try:
            self.runtime = RuntimeService(dbPath=self.dataRoot / "runtime.sqlite3",
                workspaceRoot=self.dataRoot / "jobs", logDirectory=self.dataRoot / "logs",
                pluginRootPaths=pluginRootPaths, productionMode=True)
        except Exception as error:
            raise RuntimeInitializationError(self.dataRoot, "数据库 / Runtime 服务初始化失败", error) from error
        self.server = None
        self.closed = False
        self.document = None
        self.sessions: dict[str, WorkflowSession] = {}
        self.operationLock = threading.RLock()
        self.projectOwner = None
        try:
            self.presentation = PresentationService(self.runtime, self.dataRoot / "display")
        except Exception as error:
            self.runtime.close()
            raise RuntimeInitializationError(self.dataRoot / "display", "显示缓存初始化失败", error) from error

    @property
    def address(self):
        if self.server is None:
            self.server = AioRuntimeServer(self.runtime, self.presentation)
        return f"127.0.0.1:{self.server.port}"

    @property
    def settings(self):
        if self.document is not None and self.document.production is not None:
            return self.document.production
        return ProductionSettings()

    @property
    def workflows(self):
        return self.document.workflows if self.document else {}

    def _workflowId(self, workflowId=None):
        if self.document is None:
            raise ValueError("project not loaded")
        workflowId = workflowId or self.document.entryWorkflowId
        if workflowId not in self.workflows:
            raise ValueError("workflow not found")
        self.sessions.setdefault(workflowId, WorkflowSession())
        return workflowId

    @property
    def jobId(self):
        # Legacy single-Job API; never pretend one Job represents multiple workflows.
        return next(iter(self.sessions.values())).jobId if len(self.sessions) == 1 else ""

    @property
    def startUncertain(self):
        return any(session.startUncertain for session in self.sessions.values())

    def presentationForWorkflow(self, workflowId):
        return presentationForWorkflow(self.document, workflowId)

    @serialized
    def load(self, path, *, restoreOnline=False):
        from emo_master.core.project.files import resolveProjectFile
        from emo_master.core.project.migration import loadProjectPayload, migrateProjectPayload
        from emo_master.core.project.models import ProjectDocument
        with self.runtime._projectStateLock:
            online = self.runtime.projectConfiguration.current
            restore = restoreOnline and online is not None
            selected = (Path(online['sourcePath']).resolve() if restore
                        else resolveProjectFile(Path(path).resolve(strict=True)).resolve())
            self._releaseAll()
            previousOwner = self.projectOwner
            nextOwner = (previousOwner if previousOwner is not None and previousOwner.root == selected.parent
                         else DirectoryOwner(selected.parent).acquire())
            receipt = None
            try:
                if online is None:
                    reply = self.runtime.LoadProject(pb.LoadProjectRequest(project_path=str(selected)), None)
                    if not reply.ok:
                        raise ValueError(reply.message)
                elif restore:
                    if self.runtime.onlineRecoveryError or self.runtime.projectConfiguration.publicationError:
                        raise ValueError('E_CONFIG_NOT_READY: ' + (self.runtime.onlineRecoveryError
                            or self.runtime.projectConfiguration.publicationError))
                else:
                    document = ProjectDocument.model_validate(migrateProjectPayload(loadProjectPayload(selected)))
                    receipt = self._writeLocalConfiguration(document, selected)
            except BaseException:
                if nextOwner is not previousOwner:
                    nextOwner.close()
                if online is None:
                    self.document = None
                    self.sessions.clear()
                    if previousOwner is not None:
                        previousOwner.close()
                    self.projectOwner = None
                raise
            if previousOwner is not None and previousOwner is not nextOwner:
                previousOwner.close()
            self.projectOwner = nextOwner
            self._acceptLoadedDocument()
            self._checkPublication(receipt)
            return self.document

    def _acceptLoadedDocument(self):
        self.document = self.runtime.loadedDocument
        self.sessions = {key: WorkflowSession() for key in self.document.startupWorkflows()}

    def _writeLocalConfiguration(self, document, source):
        configuration = self.runtime.projectConfiguration
        snapshot = configuration.snapshot()
        self.runtime.jobSupervisor.assertMutationAllowed()
        receipt = configuration.start(dict(operation='loadLocalProject', requestId=uuid4().hex,
            expectedRuntimeInstanceId=self.runtime.runtimeInstanceId, expectedRevision=snapshot['documentRevision'],
            localPayload=document.model_dump(mode='json'), sourcePath=str(source), replacementPolicy='stopAffected'),
            synchronous=True)
        if receipt['state'] != 'COMMITTED':
            raise ValueError(f"{receipt.get('code', 'E_CONFIG_WRITE')}: {receipt.get('message', receipt['state'])}")
        return receipt

    @staticmethod
    def _checkPublication(receipt):
        if receipt and (receipt.get('publicationError') or receipt.get('cleanupError')):
            raise RuntimeError('E_CONFIG_NOT_READY: configuration committed; ' +
                               (receipt.get('publicationError') or receipt['cleanupError']))

    @serialized
    def installPackage(self, package):
        from emo_master.core.project.runtime_package import installRuntimePackage
        with self.runtime._projectStateLock:
            self._releaseAll()
            if self.projectOwner is None:
                raise ValueError("load the destination project before updating")
            configuration = self.runtime.projectConfiguration
            if configuration.active:
                raise ValueError('E_CONFIG_WRITER_BUSY: another configuration write is pending')
            root = self.projectOwner.root
            projectFile = self.runtime.loadedProjectFile
            receipt = None
            def activate(document):
                nonlocal receipt
                receipt = self._writeLocalConfiguration(document, projectFile)
            installRuntimePackage(package, root, owner=self.projectOwner,
                registry=self.runtime.pluginScanResult.activeOperators,
                deviceTypes=self.runtime.pluginScanResult.deviceTypes, projectFile=projectFile,
                activate=activate if configuration.current is not None else None)
            if receipt is None:
                return self.load(projectFile or root)
            self._acceptLoadedDocument()
            self._checkPublication(receipt)
            return self.document

    def start(self, workflowId=None):
        with self.operationLock:
            return self._start(workflowId)

    def _start(self, workflowId=None):
        self._synchronizeOnlineDocument()
        workflowId = self._workflowId(workflowId)
        session = self.sessions[workflowId]
        self._releasePrevious(workflowId)
        page = self.presentationForWorkflow(workflowId)
        capture = bool(page and page.pageOrder)
        requestId = uuid4().hex
        request = pb.StartJobRequest(project_id=self.document.project.projectId,
            workflow_id=workflowId,
            inputs_json=json.dumps(self.settings.inputs, ensure_ascii=True),
            legacy_snapshot_policy="NONE", capture_presentation=capture,
            expected_runtime_instance_id=self.runtime.runtimeInstanceId, start_request_id=requestId)
        session.requestId = requestId
        session.startUncertain = True
        try:
            reply = self.runtime.StartJob(request, None)
        except Exception:
            # Resolve this exact admission, never issue a second Start.
            reply = self.runtime.GetStartRequest(pb.StartRequestLookup(
                runtime_instance_id=self.runtime.runtimeInstanceId, start_request_id=requestId), None)
            if not reply.job_id and reply.status != "REJECTED":
                raise
        session.startUncertain = reply.status in {"UNKNOWN", "RESET_REQUIRED"}
        session.jobId = reply.job_id
        if not reply.ok:
            raise RuntimeError(reply.message)
        return session.jobId

    def startAll(self, workflowIds=None):
        # Selection is a run command, never a second project ownership schema.
        with self.operationLock:
            if self.document is None:
                raise ValueError("project not loaded")
            keys = list(dict.fromkeys(self.document.startupWorkflows() if workflowIds is None else workflowIds))
            if not keys:
                raise ValueError("select at least one workflow")
            if any(key not in self.workflows for key in keys):
                raise ValueError("workflow not found")
            states = {key: self.status(key) for key in keys}
            additional = sum(1 for state in states.values() if state["canStart"])
            limit = self.runtime.jobSupervisor.maxConcurrentJobs
            if limit is not None and self.runtime.jobSupervisor.activeCount() + additional > limit:
                raise ValueError(f"E_MAX_CONCURRENT_JOBS: selected batch exceeds limit {limit}")
            results = {}
            for key, state in states.items():
                if not state["canStart"] and state["state"] == "RUNNING":
                    results[key] = dict(ok=True, jobId=state["jobId"], alreadyRunning=True)
                    continue
                try:
                    results[key] = dict(ok=True, jobId=self._start(key), alreadyRunning=False)
                except Exception as error:
                    results[key] = dict(ok=False, jobId=self.sessions[key].jobId, message=str(error))
            return results

    def status(self, workflowId=None):
        with self.operationLock:
            self._synchronizeOnlineDocument()
            revision = (self.runtime.projectConfiguration.current or {}).get('documentRevision')
            if self.document is None:
                return dict(state="EMPTY", jobId="", message="", canStart=False, canLoad=True, workflows={},
                            runtimeConnected=not self.closed)
            if workflowId is not None or len(self.sessions) == 1:
                key = self._workflowId(workflowId if workflowId is not None else next(iter(self.sessions)))
                state = self._workflowStatus(key)
                if workflowId is None:
                    state["workflows"] = {key: dict(state)}
                state["pagePublicationRevision"] = self._pagePublicationRevision()
                state['documentRevision'] = revision
                state["runtimeConnected"] = not self.closed
                return state
            rows = {key: self._workflowStatus(key) for key in self.sessions}
            states = {row["state"] for row in rows.values()}
            state = next((s for s in ("FAULT", "STOPPING", "RUNNING", "READY") if s in states), "READY")
            return dict(state=state, jobId="", workflows=rows,
                documentRevision=revision,
                runtimeConnected=not self.closed,
                pagePublicationRevision=self._pagePublicationRevision(),
                message="\n".join(f"{row['name']}: {row['message']}" for row in rows.values() if row["message"]),
                canStart=any(row["canStart"] for row in rows.values()), canLoad=all(row["canLoad"] for row in rows.values()))

    def _pagePublicationRevision(self):
        from emo_master.core.project.configuration import digest
        return digest({"presentation": self.document.presentation.model_dump(mode="json") if self.document.presentation else None,
                       "assets": {key: item.model_dump(mode="json") for key, item in
                                  (self.document.resources.items.items() if self.document.resources else [])
                                  if item.purpose in {"page_image", "icon"}}})

    def _synchronizeOnlineDocument(self):
        current = self.runtime.loadedDocument
        if self.document is None or current is None or self.document is current:
            return
        if current.project.projectId != self.document.project.projectId:
            return
        from emo_master.core.project.configuration import presentationExecution
        if presentationExecution(current.model_dump()) != presentationExecution(self.document.model_dump()):
            roots = {scope.entryWorkflowId for document in (self.document, current)
                     if document.presentation for scope in document.presentation.resultScopes.values()}
            affected = {record.jobId for record in self.runtime.jobRepository.all() if record.workflowId in roots}
            for jobId in tuple(affected):
                group = self.runtime.deviceManager.dispatchGroups.get(jobId)
                if group is not None:
                    affected.update(group.members)
            if any(record.jobId in affected and (not record.isTerminal or self.runtime.jobSupervisor.ownsJobResources(record.jobId))
                   for record in self.runtime.jobRepository.all()):
                return
        for key in set(self.sessions) - current.workflows.keys():
            self._releasePrevious(key)
            del self.sessions[key]
        self.document = current
        for key in current.startupWorkflows():
            self.sessions.setdefault(key, WorkflowSession())

    def _workflowStatus(self, workflowId):
        workflow, session = self.workflows[workflowId], self.sessions[workflowId]
        if not session.jobId and not session.startUncertain:
            # A direct gRPC client may have admitted this root. Its Job belongs
            # to this Runtime too; never report READY or release directory
            # ownership while that worker is still producing outputs.
            candidates = [record for record in self.runtime.jobRepository.all()
                if record.projectId == self.document.project.projectId and record.workflowId == workflowId
                and (not record.isTerminal or self.runtime.jobSupervisor.ownsJobResources(record.jobId)
                     or record.jobId in self.presentation.jobs)]
            if candidates:
                session.jobId = candidates[-1].jobId
                session.requestId = self.runtime._jobStartRequestIds.get(session.jobId, "")
        result = self._sessionStatus(session)
        result.update(workflowId=workflowId, name=workflow.name, startRequestId=session.requestId)
        return result

    def _sessionStatus(self, session):
        if session.startUncertain:
            return dict(state="FAULT", jobId=session.jobId, message="Start outcome unknown; close Runtime and verify device outputs before restarting",
                        canStart=False, canLoad=False)
        if not session.jobId:
            return dict(state="READY" if self.document else "EMPTY", jobId="", message="",
                        canStart=self.document is not None, canLoad=True)
        record = self.runtime.jobRepository.get(session.jobId)
        owned = self.runtime.jobSupervisor.ownsJobResources(session.jobId)
        idle = record is not None and record.isTerminal and not owned
        if record is None:
            state, message = "FAULT", "Job record missing"
        elif record.status == "FAILED" or record.errorCode == "E_STOP_TIMEOUT":
            state, message = "FAULT", f"{record.errorCode}: {record.message}"
        elif idle:
            state, message = "READY", record.message
        else:
            state, message = ("STOPPING" if record.status == "STOPPING" or record.isTerminal else "RUNNING"), ""
        return dict(state=state, jobId=session.jobId, message=message,
                    jobStatus=record.status if record is not None else "",
                    resourcesReleased=not owned if record is not None else None,
                    canStart=idle and self.document is not None, canLoad=idle)

    def stop(self, workflowId=None):
        with self.operationLock:
            session = self._stopTarget(workflowId)
            if session is not None:
                self._stopSession(session)

    def _stopTarget(self, workflowId):
        key = self._workflowId(workflowId) if self.document is not None else None
        if key is None:
            return None
        session = self.sessions[key]
        if session.startUncertain:
            reply = self.runtime.GetStartRequest(pb.StartRequestLookup(
                runtime_instance_id=self.runtime.runtimeInstanceId, start_request_id=session.requestId), None)
            if reply.job_id:
                session.jobId, session.startUncertain = reply.job_id, False
            elif reply.status == "REJECTED":
                session.startUncertain = False
            else:
                raise RuntimeError("Start outcome unknown; close Runtime instead of guessing the workflow Job")
        return session

    def _stopSession(self, session):
        deadline = self._requestStopSession(session)
        if deadline is not None:
            self._waitRetired(session, deadline)

    def _requestStopSession(self, session):
        if not session.jobId:
            return
        reply = self.runtime.StopJob(pb.StopJobRequest(job_id=session.jobId, mode="graceful"), None)
        if not reply.ok:
            raise RuntimeError(reply.message)
        grace = self.document.runtime.gracefulStopTimeoutMs / 1000 if self.document else 5
        return time.monotonic() + grace + 5

    def stopAll(self):
        results = {}
        pending = []
        with self.operationLock:
            # Signal every owned Job before waiting for any one process. Five
            # cold/slow workers must not accumulate five serial grace periods.
            # Per-Job deadlines and failed/unknown ownership stay unchanged.
            for key in self.sessions:
                try:
                    session = self._stopTarget(key)
                    deadline = self._requestStopSession(session) if session is not None else None
                    if deadline is not None:
                        pending.append((key, session, deadline))
                    results[key] = dict(ok=True, jobId=self.sessions[key].jobId)
                except Exception as error:
                    results[key] = dict(ok=False, jobId=self.sessions[key].jobId, message=str(error))
            for key, session, deadline in pending:
                try:
                    self._waitRetired(session, deadline)
                except Exception as error:
                    results[key] = dict(ok=False, jobId=session.jobId, message=str(error))
        return results

    def _waitRetired(self, session, deadline):
        while self.runtime.jobSupervisor.ownsJobResources(session.jobId):
            if time.monotonic() >= deadline:
                raise TimeoutError("Worker still owns Runtime resources")
            time.sleep(.02)

    def _releasePrevious(self, workflowId):
        session = self.sessions[workflowId]
        if session.startUncertain:
            raise RuntimeError("Start outcome unknown; do not issue another Start")
        if not session.jobId:
            return
        record = self.runtime.jobRepository.get(session.jobId)
        if record is None or not record.isTerminal:
            raise ValueError("stop the current Job before loading or starting")
        deadline = time.monotonic() + 5
        self._waitRetired(session, deadline)
        while True:
            try:
                self.presentation.release(session.jobId)
                break
            except ValueError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(.02)
        session.jobId = ""

    def _releaseAll(self):
        # Check every workflow before releasing any; a reload cannot partially
        # discard display state and then discover that another workflow is busy.
        if self.document is not None:
            for record in self.runtime.jobRepository.all():
                if record.projectId == self.document.project.projectId and (not record.isTerminal or self.runtime.jobSupervisor.ownsJobResources(record.jobId)):
                    self._workflowId(record.workflowId)
        for key in self.sessions:
            self._workflowStatus(key)
        for session in self.sessions.values():
            if session.startUncertain:
                raise RuntimeError("Start outcome unknown; do not reload")
            if session.jobId:
                record = self.runtime.jobRepository.get(session.jobId)
                if record is None or not record.isTerminal:
                    raise ValueError("stop the current Job before loading or starting")
        for key in self.sessions:
            self._releasePrevious(key)

    @serialized
    def close(self):
        if self.closed:
            return
        if self.server is not None:
            self.server.close()
            self.server = None
        if self.document is not None:
            for record in self.runtime.jobRepository.all():
                if record.projectId == self.document.project.projectId and (not record.isTerminal or self.runtime.jobSupervisor.ownsJobResources(record.jobId)):
                    self._workflowId(record.workflowId)
        for key in self.sessions:
            self._workflowStatus(key)
        self.stopAll()
        # Runtime.close is the authority even when an exact Start outcome is
        # unknown. It stops every owned worker, not just recorded UI sessions.
        self.runtime.close()
        if self.projectOwner is not None:
            self.projectOwner.close()
            self.projectOwner = None
        self.closed = True
