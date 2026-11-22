"""Execution and paging state for one Runtime Job's loaded log events."""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from emo_master.apps.designer.ui.log_dialog import StructuredLogEntry


BranchKey = tuple[str | int, ...]
EntryPredicate = Callable[["StructuredLogEntry"], bool]
IterationKey = tuple[str, str, tuple[int, ...]]
_RUNNING = {"RUNNING", "STOPPING"}
_WORKFLOW_START = "workflow.started"
_WORKFLOW_END = {"workflow.completed", "workflow.failed"}
_NODE_START = "node.started"
_NODE_END = {"node.completed", "node.failed", "node.skipped"}
_LOOP_START = "loop.started"
_LOOP_END = {"loop.completed", "loop.failed", "loop.timeout", "loop.limit_reached"}
_ITERATION_START = "loop.iteration.started"
_ITERATION_END = "loop.iteration.completed"


class JobEventWindow:
    """The loaded raw events only. Earlier and live merges trim opposite ends."""

    def __init__(self, jobId: str, maximumEntries: int = 10_000):
        self.jobId = jobId
        self.maximumEntries = max(1, maximumEntries)
        self.paused = False
        self.newCount = 0
        self.trimmed = False
        self.liveTrimmed = False
        self._entries: dict[int, StructuredLogEntry] = {}
        self._observedEventId = 0

    @property
    def entries(self) -> tuple[StructuredLogEntry, ...]:
        return tuple(self._entries.values())

    @property
    def firstEventId(self) -> int | None:
        return next(iter(self._entries), None)

    @property
    def lastEventId(self) -> int | None:
        return next(reversed(self._entries), None)

    def _owned(self, entries: Iterable[StructuredLogEntry]) -> dict[int, StructuredLogEntry]:
        return {
            entry.eventId: entry for entry in entries
            if entry.eventId and entry.eventId > 0 and entry.effectiveJobId == self.jobId
        }

    def mergeRecent(self, entries: Iterable[StructuredLogEntry]) -> None:
        rows = self._owned(entries)
        keys = sorted(rows)
        self.trimmed = len(keys) > self.maximumEntries
        self.liveTrimmed = self.trimmed
        self._entries = {key: rows[key] for key in keys[-self.maximumEntries:]}
        self._observedEventId = keys[-1] if keys else 0
        self.paused = False
        self.newCount = 0

    def pause(self) -> None:
        self.paused = True

    def appendLive(self, entries: Iterable[StructuredLogEntry], *, truncated: bool = False) -> bool:
        rows = self._owned(entries)
        if truncated:
            self.trimmed = self.liveTrimmed = True
        if not rows:
            return False
        previous = self._observedEventId
        self._observedEventId = max(previous, max(rows))
        if self.paused:
            self.newCount += sum(key > previous for key in rows)
            return False
        if rows.keys() <= self._entries.keys():
            return False
        rows.update(self._entries)
        keys = sorted(rows)
        if len(keys) > self.maximumEntries:
            self.trimmed = self.liveTrimmed = True
        kept = keys[-self.maximumEntries:]
        changed = kept != list(self._entries)
        self._entries = {key: rows[key] for key in kept}
        return changed

    def mergeEarlier(self, entries: Iterable[StructuredLogEntry], *, protected: Iterable[int | None] = ()) -> bool:
        self.pause()
        earlier = self._owned(entries)
        rows = {**earlier, **self._entries}
        keys = sorted(rows)
        kept = keys[:self.maximumEntries]
        required = (set(protected) & self._entries.keys()) | (earlier.keys() - self._entries.keys())
        if not required <= set(kept):
            return False
        self.trimmed |= len(keys) > self.maximumEntries
        self._entries = {key: rows[key] for key in kept}
        return True

    def replaceEarlier(self, entries: Iterable[StructuredLogEntry]) -> None:
        rows = self._owned(entries)
        self._entries = {key: rows[key] for key in sorted(rows)[-self.maximumEntries:]}
        self.trimmed = True
        self.pause()


@dataclass(frozen=True)
class ExecutionTime:
    startedAtMs: int | None = None
    endedAtMs: int | None = None
    acceptedAtMs: int | None = None
    confirmedAtMs: int | None = None
    elapsedMs: int | None = None
    estimated: bool = False
    note: str = ""

    def display(self, showRange: bool = False) -> str:
        parts = []
        if showRange:
            parts.append(f"{_clockText(self.startedAtMs)} 至 {_clockText(self.endedAtMs) if not self.estimated else '进行中'}")
            if self.confirmedAtMs is not None:
                parts.append("重启确认 " + _clockText(self.confirmedAtMs))
        if self.elapsedMs is not None:
            elapsed = f"{self.elapsedMs:,} ms" if self.elapsedMs < 1000 else f"{self.elapsedMs / 1000:,.2f} s"
            parts.append(f"估算 {elapsed}" if self.estimated else elapsed)
        if self.note:
            parts.append(self.note)
        return " · ".join(parts) or "时间未记录"


def _clockText(timestampMs: int | None) -> str:
    if timestampMs is None:
        return "未记录"
    return datetime.fromtimestamp(timestampMs / 1000).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def _milliseconds(value: object) -> int | None:
    return value if type(value) is int and value > 0 else None


def _elapsed(start: int | None, end: int | None) -> int | None:
    return end - start if start is not None and end is not None and end >= start else None


def _now(nowMs: int | None) -> int:
    return nowMs if nowMs is not None else time.time_ns() // 1_000_000


def _jobField(summary: object, wire: str, key: str) -> object:
    return summary.get(key, summary.get(wire)) if isinstance(summary, Mapping) else getattr(summary, wire, None)


def jobExecutionTime(jobSummary: object, nowMs: int | None = None) -> ExecutionTime:
    start = _milliseconds(_jobField(jobSummary, "started_at_ms", "startedAtMs"))
    end = _milliseconds(_jobField(jobSummary, "ended_at_ms", "endedAtMs"))
    accepted = _milliseconds(_jobField(jobSummary, "accepted_at_ms", "acceptedAtMs"))
    if accepted is None:
        accepted = _milliseconds(_jobField(jobSummary, "start_at_ms", "startAtMs"))
    status = _jobField(jobSummary, "status", "status")
    errorCode = _jobField(jobSummary, "error_code", "errorCode")
    if errorCode == "E_RUNTIME_RESTARTED":
        return ExecutionTime(start, acceptedAtMs=accepted, confirmedAtMs=end, note="执行中断，实际结束时间未记录")
    running = status in _RUNNING and end is None
    elapsed = _elapsed(start, _now(nowMs) if running else end)
    note = ""
    if elapsed is None:
        note = "等待实际开始" if status in {"ACCEPTED", "STARTING"} and start is None else "时间信息不完整，原因未确认"
    return ExecutionTime(start, end, accepted, elapsedMs=elapsed, estimated=running and elapsed is not None, note=note)


@dataclass
class ExecutionBranch:
    key: BranchKey
    kind: str
    title: str
    children: list[ExecutionBranch] = field(default_factory=list)
    entry: StructuredLogEntry | None = None
    start: StructuredLogEntry | None = None
    end: StructuredLogEntry | None = None
    mode: str = ""
    totalIterations: int | None = None
    bodyExecuted: bool | None = None
    completedIterations: int = 0

    def executionTime(self, active: bool = False, hasEarlier: bool = False,
                      nowMs: int | None = None) -> ExecutionTime:
        start = self.start.rawTimestampMs if self.start else None
        end = self.end.rawTimestampMs if self.end else None
        running = active and self.end is None
        elapsed = _elapsed(start, _now(nowMs) if running else end)
        note = ""
        if elapsed is None and self.kind in {"workflow", "iteration", "node", "loop"}:
            if self.end and self.end.eventType == "node.skipped" and self.start is None:
                note = "节点已跳过，未记录执行开始"
            elif (self.start and start is None) or (self.end and end is None):
                note = "生命周期记录未包含有效的原始时间"
            elif hasEarlier and (self.start is None or self.end is None):
                note = "配对事件尚未加载完整"
            else:
                note = "时间信息不完整，原因未确认"
        return ExecutionTime(start, end, elapsedMs=elapsed, estimated=running and elapsed is not None, note=note)


class ExecutionTree:
    """Build associations from the loaded originals before applying display filters."""

    def __init__(self, entries: Iterable[StructuredLogEntry]):
        self._parents: dict[BranchKey, ExecutionBranch] = {}
        self._events: dict[int, ExecutionBranch] = {}
        self._branches: dict[BranchKey, ExecutionBranch] = {}
        self._unlinked = ExecutionBranch(("unlinked",), "unlinked", "未完整关联事件")
        self.rootCount = 0
        rows = sorted(entries, key=lambda entry: entry.eventId or 0)
        byRun: dict[str, list[StructuredLogEntry]] = defaultdict(list)
        nodesByCaller: dict[IterationKey, set[BranchKey]] = defaultdict(set)
        iterations: dict[IterationKey, ExecutionBranch] = {}
        loops: dict[BranchKey, ExecutionBranch] = {}

        for entry in rows:
            if not entry.workflowRunId:
                continue
            byRun[entry.workflowRunId].append(entry)
            if entry.nodeRunId and entry.nodeId and entry.iterationPathRecorded and not entry.eventType.startswith("loop.iteration."):
                nodesByCaller[entry.workflowRunId, entry.nodeId, entry.iterationPath].add(self._nodeKey(entry))

        for runId, runEntries in byRun.items():
            workflow = self._workflow(runId)
            confirmedName = next((entry.workflowName for entry in runEntries if entry.workflowName), "")
            workflowId = next((entry.workflowId for entry in runEntries if entry.workflowId), "")
            workflow.title = confirmedName or workflowId or "工作流名称未记录"
            for entry in runEntries:
                if entry.eventType == _WORKFLOW_START:
                    workflow.start = entry
                elif entry.eventType in _WORKFLOW_END:
                    workflow.end = entry
                if entry.eventType in {_LOOP_START, *_LOOP_END, _ITERATION_START, _ITERATION_END}:
                    self._addLoopEvidence(entry, nodesByCaller, loops, iterations)

        for entry in rows:
            owningWorkflow = self._branches.get(("workflow", entry.workflowRunId))
            iteration = iterations.get((entry.workflowRunId, entry.nodeId, entry.iterationPath)) if entry.iterationPathRecorded else None
            if owningWorkflow is None:
                parent = self._unlinked
            elif entry.eventType in {_WORKFLOW_START, *_WORKFLOW_END}:
                parent = owningWorkflow
            elif iteration is not None:
                parent = iteration
            elif entry.eventType in {_ITERATION_START, _ITERATION_END}:
                parent = self._unlinked
            elif entry.nodeRunId:
                parent = self._branch(self._nodeKey(entry), "node", entry.displayNode() or entry.nodeRunId)
                if entry.eventType == _NODE_START:
                    parent.start = entry
                elif entry.eventType in _NODE_END:
                    parent.end = entry
                self._attach(parent, owningWorkflow)
            elif entry.eventType in {_LOOP_START, *_LOOP_END} and entry.iterationPathRecorded:
                parent = loops.get(self._loopKey(entry, entry.iterationPath, nodesByCaller)) or self._unlinked
            elif entry.nodeId or not entry.iterationPathRecorded:
                parent = self._branch(("unlinked", entry.workflowRunId), "unlinked", "未完整关联事件")
                self._attach(parent, owningWorkflow)
            else:
                parent = owningWorkflow
            self._addEvent(parent, entry)

        for runId, runEntries in byRun.items():
            workflow = self._workflow(runId)
            relation = workflow.start or workflow.end
            parentId = next((entry.parentWorkflowRunId for entry in runEntries if entry.parentWorkflowRunId), "")
            if parentId and parentId != runId:
                parent = self._workflow(parentId)
                caller = relation.nodeId if relation else ""
                if caller and relation and relation.iterationPathRecorded:
                    iterationKey = (parentId, caller, relation.iterationPath)
                    if iterationKey in iterations:
                        parent = iterations[iterationKey]
                    else:
                        keys = nodesByCaller.get(iterationKey, set())
                        if len(keys) == 1:
                            parent = self._branches.get(next(iter(keys)), parent)
                self._attach(workflow, parent)
            elif any(entry.parentWorkflowRunIdRecorded for entry in runEntries) and not parentId:
                self.rootCount += 1
            else:
                self._attach(workflow, self._unlinked)

        for loop in loops.values():
            self._describeLoop(loop)
        roots = [branch for branch in self._branches.values() if branch.key not in self._parents]
        if self._unlinked.children:
            roots.append(self._unlinked)
        self._sortBranches(roots)
        self.roots = tuple(roots)

    @staticmethod
    def _nodeKey(entry: StructuredLogEntry) -> BranchKey:
        return "node", entry.workflowRunId, entry.nodeRunId

    def _branch(self, key: BranchKey, kind: str, title: str) -> ExecutionBranch:
        if key not in self._branches:
            self._branches[key] = ExecutionBranch(key, kind, title)
        return self._branches[key]

    def _workflow(self, runId: str) -> ExecutionBranch:
        return self._branch(("workflow", runId), "workflow", "父调用详情未加载")

    def _attach(self, branch: ExecutionBranch, parent: ExecutionBranch) -> None:
        if branch.key in self._parents:
            return
        cursor = parent
        while cursor.key in self._parents and cursor.key != branch.key:
            cursor = self._parents[cursor.key]
        if cursor.key == branch.key:
            return
        parent.children.append(branch)
        self._parents[branch.key] = parent

    def _addEvent(self, parent: ExecutionBranch, entry: StructuredLogEntry) -> None:
        key = ("event", entry.eventId if entry.eventId is not None else -len(parent.children) - 1)
        branch = ExecutionBranch(key, "event", entry.displayMessage(), entry=entry)
        parent.children.append(branch)
        if entry.eventId is not None:
            self._parents[key] = parent
            self._events[entry.eventId] = branch

    def _loopKey(self, entry: StructuredLogEntry, path: tuple[int, ...],
                 nodesByCaller: Mapping[IterationKey, set[BranchKey]]) -> BranchKey:
        if entry.nodeRunId:
            return self._nodeKey(entry)
        candidates = nodesByCaller.get((entry.workflowRunId, entry.nodeId, path), set())
        if len(candidates) == 1:
            return next(iter(candidates))
        return ("loop", entry.workflowRunId, entry.nodeId, *path)

    def _addLoopEvidence(self, entry: StructuredLogEntry,
                         nodesByCaller: Mapping[IterationKey, set[BranchKey]],
                         loops: dict[BranchKey, ExecutionBranch],
                         iterations: dict[IterationKey, ExecutionBranch]) -> None:
        isIteration = entry.eventType in {_ITERATION_START, _ITERATION_END}
        if not entry.nodeId or not entry.iterationPathRecorded or (isIteration and not entry.iterationPath):
            return
        path = entry.iterationPath
        loopKey = self._loopKey(entry, path[:-1] if isIteration else path, nodesByCaller)
        loop = self._branch(loopKey, "loop", entry.displayNode())
        loops[loopKey] = loop
        self._attach(loop, self._workflow(entry.workflowRunId))
        if not isIteration:
            if entry.eventType == _LOOP_START:
                mode = entry.payload.get("mode")
                if isinstance(mode, str):
                    loop.mode = mode
                total = entry.payload.get("totalIterations")
                if loop.mode in {"foreach", "repeat"} and type(total) is int and total >= 0:
                    loop.totalIterations = total
            return
        iteration = self._branch(("iteration", entry.workflowRunId, entry.nodeId, *path), "iteration", "")
        iterations[entry.workflowRunId, entry.nodeId, path] = iteration
        self._attach(iteration, loop)
        if entry.eventType == _ITERATION_START:
            iteration.start = entry
        else:
            iteration.end = entry
            body = entry.payload.get("bodyExecuted")
            iteration.bodyExecuted = body if type(body) is bool else None

    @staticmethod
    def _describeLoop(loop: ExecutionBranch) -> None:
        completedIndices = []
        for child in loop.children:
            if child.kind != "iteration":
                continue
            child.mode = loop.mode
            index = int(child.key[-1]) + 1
            if child.bodyExecuted is False:
                child.title = f"第 {index} 次 · 退出条件检查（未进入循环体）"
            elif child.bodyExecuted is True or loop.mode in {"foreach", "repeat"}:
                child.title = f"第 {index} 轮"
            else:
                child.title = f"第 {index} 次迭代 · 执行情况未记录"
            if child.end and (child.bodyExecuted is True or loop.mode in {"foreach", "repeat"}):
                completedIndices.append(index)
        loop.completedIterations = len(completedIndices)
        modeLabels = {"while": "While", "foreach": "ForEach", "repeat": "Repeat"}
        label = modeLabels.get(loop.mode, loop.mode or "循环类型未记录")
        info = f"已加载 {loop.completedIterations} 轮完成"
        if loop.totalIterations is not None:
            # ForEach and Repeat process consecutive indices. A recorded completion
            # provides progress even when earlier per-iteration rows fell out of the window.
            info = f"已完成 {max(completedIndices, default=0)} / {loop.totalIterations} 项"
        elif loop.mode != "while":
            info += " · 总数未记录"
        loop.title = f"{loop.title} · {label} · {info}"

    @classmethod
    def _sortBranches(cls, branches: list[ExecutionBranch]) -> None:
        def firstId(branch: ExecutionBranch) -> int:
            entry = branch.entry or branch.start
            if entry and entry.eventId is not None:
                return entry.eventId
            return firstId(branch.children[0]) if branch.children else 0

        for branch in branches:
            cls._sortBranches(branch.children)
        branches.sort(key=firstId)

    def project(self, matches: EntryPredicate) -> tuple[ExecutionBranch, ...]:
        def visible(branch: ExecutionBranch) -> ExecutionBranch | None:
            if branch.entry is not None:
                return branch if matches(branch.entry) else None
            children = [child for item in branch.children if (child := visible(item)) is not None]
            return replace(branch, children=children) if children else None

        return tuple(branch for root in self.roots if (branch := visible(root)) is not None)

    def branchKeys(self) -> set[BranchKey]:
        return {*self._branches, self._unlinked.key}

    def branchForEvent(self, eventId: int | None) -> ExecutionBranch | None:
        event = self._events.get(eventId) if eventId is not None else None
        return self._parents.get(event.key) if event is not None else None

    def representativeEventId(self, key: BranchKey) -> int | None:
        if key[0] == "event" and isinstance(key[1], int):
            return key[1] if key[1] in self._events else None
        branch = self._unlinked if key == self._unlinked.key else self._branches.get(key)
        while branch is not None:
            event = branch.entry or branch.start or branch.end
            if event is not None and event.eventId is not None:
                return event.eventId
            branch = branch.children[0] if branch.children else None
        return None

    def contextMatches(self, eventId: int) -> EntryPredicate | None:
        branch = self._events.get(eventId)
        while branch is not None and branch.kind != "iteration":
            branch = self._parents.get(branch.key)
        if branch is None:
            return None
        target = branch.key

        def matches(entry: StructuredLogEntry) -> bool:
            parent = self._events.get(entry.eventId) if entry.eventId is not None else None
            while parent is not None and parent.key != target:
                parent = self._parents.get(parent.key)
            return parent is not None

        return matches

    def activeKeys(self, *, jobStatus: str, runtimeInstanceId: str, continuous: bool) -> set[BranchKey]:
        if jobStatus not in _RUNNING or not runtimeInstanceId or not continuous:
            return set()

        active = set()

        def collect(branch: ExecutionBranch) -> None:
            if branch.end is not None:
                return
            if branch.start is not None and branch.start.runtimeInstanceId == runtimeInstanceId:
                active.add(branch.key)
            for child in branch.children:
                collect(child)

        for root in self.roots:
            collect(root)
        return active

    def currentNode(self, *, jobStatus: str, runtimeInstanceId: str, continuous: bool) -> StructuredLogEntry | None:
        active = self.activeKeys(jobStatus=jobStatus, runtimeInstanceId=runtimeInstanceId, continuous=continuous)
        candidates = {key for key in active if self._branches[key].kind in {"node", "loop"}}
        ancestors = set()
        for key in candidates:
            parent = self._parents.get(key)
            while parent is not None:
                ancestors.add(parent.key)
                parent = self._parents.get(parent.key)
        remaining = candidates - ancestors
        if len(remaining) != 1:
            return None
        return self._branches[next(iter(remaining))].start
