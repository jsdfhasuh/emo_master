"""Batch commands over existing single-Job controllers, with Job-scoped views."""
from dataclasses import dataclass
from inspect import signature

from .runtime_controller import RuntimeController
from emo_master.apps.designer.ui.runtime_panel import RuntimePanelState


@dataclass
class WorkflowRun:
    workflowId: str
    state: RuntimePanelState
    jobId: str | None = None
    controller: RuntimeController | None = None


class WorkflowRuntimeController:
    def __init__(self, *, getRunTargets, getWorkflows, getMaxJobs, selectState,
                 inspectionRouter=None, **callbacks):
        self.callbacks = callbacks
        self.getRunTargets, self.getWorkflows, self.getMaxJobs = getRunTargets, getWorkflows, getMaxJobs
        self.selectState = selectState
        self.inspectionRouter = inspectionRouter
        self.syncRuntimeProjectBeforeRun = callbacks['syncRuntimeProjectBeforeRun']
        self.getCapturePresentation = callbacks.get('getCapturePresentation', lambda: False)
        self.runs = {}
        self.selected = ''
        self._closing = self._closed = False
        self._batchStarting = False
        self._create('', callbacks['runtimePanelState'])

    def __getattr__(self, name):
        # Keep the legacy single-Job inspector/test adapter usable.
        runs = self.__dict__.get('runs', {})
        selected = self.__dict__.get('selected', '')
        if selected in runs:
            return getattr(runs[selected].controller, name)
        raise AttributeError(name)

    @property
    def _jobActive(self):
        return any(run.controller._jobActive for run in self.runs.values())

    def busy(self, run):
        child = run.controller
        return (child._jobActive or child._startUncertain or child._worker is not None
                or child._stopWorker is not None)

    def canStart(self):
        if self._closing or self._closed or self._batchStarting:
            return False
        return any(key not in self.runs or not self.busy(self.runs[key]) for key in self.targets())

    def canStop(self):
        run = self.runs.get(self.selected)
        return run is not None and self.busy(run)

    def targets(self):
        return list(dict.fromkeys(self.getRunTargets() or [self.callbacks['getEntryWorkflowId']()]))

    def _changed(self):
        if self._closed:
            return
        self.callbacks['setIsJobRunning'](any(self.busy(run) for run in self.runs.values()) or self._batchStarting)
        self.callbacks['updateToolbarState']()

    def _isSelected(self, run):
        return self.runs.get(self.selected) is run

    def _refresh(self, run):
        if self._isSelected(run) and not self._closing:
            self.callbacks['refreshRuntimePanelView']()
        self._changed()

    def _setJob(self, run, jobId):
        run.jobId = jobId
        run.state.setActiveJob(jobId)
        if self._isSelected(run):
            self.callbacks['setCurrentJobId'](jobId)

    def _inspection(self, method, run, *args):
        router = self.inspectionRouter
        if router is not None:
            return router.forWorkflow(method, run.workflowId, *args)
        callback = self.callbacks.get(method)
        return callback(*args) if callback else None

    def _create(self, key, state=None):
        run = WorkflowRun(key, state or RuntimePanelState())
        self.runs[key] = run
        callbacks = dict(self.callbacks)
        callbacks.update(runtimePanelState=run.state,
            appendLog=lambda level, message: self.callbacks['appendLog'](level,
                f'[{run.workflowId or "入口"} · {run.jobId or "待启动"}] {message}'),
            getCurrentJobId=lambda: run.jobId or (self.callbacks['getCurrentJobId']() if self._isSelected(run) else None),
            setCurrentJobId=lambda job: self._setJob(run, job),
            setIsJobRunning=lambda _: self._changed(),
            refreshRuntimePanelView=lambda: self._refresh(run),
            updateToolbarState=self._changed,
            syncRuntimeProjectBeforeRun=lambda: True,
            getEntryWorkflowId=lambda: run.workflowId or self.callbacks['getEntryWorkflowId'](),
            getCapturePresentation=lambda: (self.getCapturePresentation(run.workflowId)
                if 'workflowId' in signature(self.getCapturePresentation).parameters else self.getCapturePresentation()),
            applyRuntimeEventToNode=lambda event: self.callbacks['applyRuntimeEventToNode'](event),
            onInspectionStarting=lambda: self._inspection('onInspectionStarting', run),
            onInspectionAccepted=lambda reply: self._inspection('onInspectionAccepted', run, reply),
            onInspectionEvent=lambda event: self._inspection('onInspectionEvent', run, event),
            onInspectionStatus=lambda reply: self._inspection('onInspectionStatus', run, reply),
            onInspectionClosing=None)
        run.controller = RuntimeController(**callbacks)
        return run

    def select(self, workflowId):
        if workflowId not in self.runs:
            return
        self.selected = workflowId
        run = self.runs[workflowId]
        self.selectState(run.state, run.jobId)
        if self.inspectionRouter:
            self.inspectionRouter.selectWorkflow(workflowId)
        self._refresh(run)

    def startJob(self):
        if not self.canStart():
            return
        keys = self.targets()
        if any(key not in self.getWorkflows() for key in keys):
            self.callbacks['appendLog']('ERROR', '运行目标已不存在，请重新选择')
            return
        # Adopt the legacy initial state once the actual entry is known.
        if '' in self.runs:
            initial = self.runs.pop('')
            initial.workflowId = self.callbacks['getEntryWorkflowId']()
            self.runs[initial.workflowId] = initial
            self.selected = initial.workflowId
        pending = [key for key in keys if key not in self.runs or not self.busy(self.runs[key])]
        limit = self.getMaxJobs()
        active = sum(self.busy(run) for run in self.runs.values())
        if limit is not None and active + len(pending) > limit:
            self.callbacks['appendLog']('ERROR', f'所选流程超过最大并发 Job 数 {limit}，未启动本批次')
            return
        # One save/load for the first batch; NEVER reload under a live sibling.
        if not active and not self.syncRuntimeProjectBeforeRun():
            return
        self._batchStarting = True
        try:
            for key in pending:
                if key not in self.runs:
                    self._create(key)
            if self.selected not in keys:
                self.select(keys[0])
            elif self.inspectionRouter:
                self.inspectionRouter.selectWorkflow(self.selected)
            for key in pending:
                try:
                    self.runs[key].controller.startJob()
                except Exception as error:
                    self.callbacks['appendLog']('ERROR', f'{key} 启动失败：{error}；其他流程保持原状')
        finally:
            self._batchStarting = False
            self._changed()

    def stopJob(self, mode=None):
        run = self.runs.get(self.selected)
        if run:
            run.controller.stopJob(mode)

    def stopAll(self):
        for run in self.runs.values():
            if self.busy(run):
                try:
                    run.controller.stopJob()
                except Exception as error:
                    self.callbacks['appendLog']('ERROR', f'{run.workflowId} 停止请求失败：{error}；继续停止其他流程')

    def reset(self):
        if any(self.busy(run) for run in self.runs.values()):
            raise ValueError('请先停止所有流程')
        self.runs.clear()
        self.selected = ''
        run = self._create('')
        self.selectState(run.state, None)

    def close(self):
        if self._closed:
            return
        self._closing = True
        self.stopAll()
        errors = []
        for run in self.runs.values():
            try:
                run.controller.close(closeClient=False)
            except Exception as error:
                errors.append(error)
        if errors:
            raise errors[0]
        if self.inspectionRouter:
            self.inspectionRouter.close()
        elif self.callbacks.get('onInspectionClosing'):
            self.callbacks['onInspectionClosing']()
        close = getattr(self.callbacks['runtimeClient'], 'close', None)
        if callable(close):
            close()
        self._closed, self._closing = True, False
