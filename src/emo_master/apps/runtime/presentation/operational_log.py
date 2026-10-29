"""Job-local display diagnostics through the ordinary Runtime event store."""
from dataclasses import dataclass
from collections import OrderedDict
import time

from emo_master.core.presentation.messages import sourceIssue


@dataclass
class _Issue:
    kind: str
    level: str
    message: str
    firstAt: float
    lastLoggedAt: float
    count: int = 0
    reported: int = 0
    healthySince: float | None = None
    context: dict | None = None


class PresentationLog:
    """Only failed sources retain state; normal frames do not produce logs."""

    def __init__(self, publish, projectId, *, clock=time.monotonic, interval=30.0, recovery=1.0):
        self.publish = publish
        self.projectId = projectId
        self.clock = clock
        self.interval = interval
        self.recovery = recovery
        self.issues = {}
        self.latestOrdinals = {}
        self.lateIssues = {}
        self.seenFailures = OrderedDict()
        self.finished = False

    def observe(self, item, sources, diagnostic=None):
        if self.finished:
            return
        now = self.clock()
        identity = item['identity']
        for source in sources:
            sourceId = source['sourceId']
            key = (identity['resultScopeId'], sourceId)
            ordinal = identity['resultOrdinal']
            latest = self.latestOrdinals.get(key, -1)
            code, reason = source.get('reasonCode', ''), source.get('reason', '')
            # Node failures already have authoritative node.failed events.
            issue = (None if source['state'] == 'AVAILABLE' or code == 'NODE_FAILED'
                     else sourceIssue(code, reason))
            if issue is not None:
                failureKey = (*key, ordinal)
                if failureKey in self.seenFailures:
                    continue
                self.seenFailures[failureKey] = None
                if len(self.seenFailures) > 512:
                    self.seenFailures.popitem(last=False)
            if ordinal <= latest:
                # Current display state is monotonic, but older async exports
                # still carry failure evidence. Never let them change recovery.
                if issue is not None and ordinal < latest:
                    self._lateFailure(key, issue, item, source, diagnostic, now)
                continue
            self.latestOrdinals[key] = ordinal
            previous = self.issues.get(key)
            if source['state'] == 'AVAILABLE':
                if previous is not None:
                    previous.context = self._context(item, source, diagnostic)
                    if previous.healthySince is None:
                        previous.healthySince = now
                    if now - previous.healthySince >= self.recovery:
                        self.issues.pop(key)
                        self._emit(previous, recovered=True)
                continue
            if issue is None:
                if previous is not None:
                    previous.healthySince = None
                continue
            kind, level, message = issue
            changed = previous is None or previous.kind != kind
            if changed:
                if previous is not None and previous.count > previous.reported:
                    self._emit(previous)
                previous = _Issue(kind, level, message, now, now)
                self.issues[key] = previous
            previous.count += 1
            previous.context = self._context(item, source, diagnostic)
            previous.healthySince = None
            previous.message = message
            if changed or now - previous.lastLoggedAt >= self.interval:
                previous.lastLoggedAt = now
                self._emit(previous)

    def _lateFailure(self, key, description, item, source, diagnostic, now):
        kind, level, message = description
        lateKey = (*key, kind)
        issue = self.lateIssues.get(lateKey)
        if issue is None:
            issue = self.lateIssues[lateKey] = _Issue(kind, level, message, now, now)
        issue.count += 1
        issue.message = message
        issue.context = dict(self._context(item, source, diagnostic), lateResult=True,
                             currentResultOrdinal=self.latestOrdinals[key])
        if issue.count == 1 or now - issue.lastLoggedAt >= self.interval:
            issue.lastLoggedAt = now
            self._emit(issue)

    def finish(self):
        if self.finished:
            return
        self.finished = True
        for issue in self.issues.values():
            if issue.healthySince is not None:
                self._emit(issue, recovered=True)
            elif issue.count > issue.reported:
                self._emit(issue)
        self.issues.clear()
        for issue in self.lateIssues.values():
            if issue.count > issue.reported:
                self._emit(issue)
        self.lateIssues.clear()
        self.seenFailures.clear()
        self.latestOrdinals.clear()

    def _context(self, item, source, diagnostic):
        sourceId = source['sourceId']
        origin = item.get('sourceOrigins', {}).get(sourceId, {})
        observation = item.get('sourceObservations', {}).get(sourceId, {})
        return dict(identity=dict(item['identity']), sourceId=sourceId,
                    workflowId=origin.get('workflowId') or item.get('workflowId', ''),
                    nodeId=origin.get('nodeId') or '', port=origin.get('port') or '',
                    workflowRunId=observation.get('workflowRunId') or item['identity'].get('invocationId', ''),
                    nodeRunId=observation.get('nodeRunId', ''),
                    iterationPath=observation.get('iterationPath', item.get('iterationPath', [])),
                    reasonCode=source.get('reasonCode') or '', reason=(source.get('reason') or '')[:1024],
                    capture=observation.get('capture', {}),
                    exports=[entry for entry in (diagnostic or {}).get('exports', [])
                             if entry.get('sourceId') == sourceId],
                    validationError=(diagnostic or {}).get('error'))

    def _emit(self, issue, recovered=False):
        context = issue.context
        identity = context['identity']
        count = issue.count - issue.reported
        # A failed log sink must not turn every following frame into a retry.
        issue.reported = issue.count
        level = 'INFO' if recovered else issue.level
        if not recovered and issue.kind == 'IMAGE_BUSY' and issue.count > 1:
            level = 'WARN'
        message = ('页面展示已恢复' if recovered else issue.message.replace('\n', '；'))
        if context.get('lateResult'):
            message = '较早结果的导出诊断：' + message
        message += f"；数据源 {context['sourceId']}；累计 {issue.count} 次不可用"
        payload = dict(context, issueKind=issue.kind, occurrences=issue.count,
                       occurrencesSinceLastLog=count, durationMs=int((self.clock() - issue.firstAt) * 1000),
                       previousIssue=issue.message if recovered else '')
        self.publish(identity['jobId'],
                     'presentation.source.recovered' if recovered else 'presentation.source.unavailable',
                     message, level=level, code='PRESENTATION_RECOVERED' if recovered else issue.kind,
                     projectId=self.projectId, workflowId=context['workflowId'], nodeId=context['nodeId'],
                     workflowRunId=context['workflowRunId'], nodeRunId=context['nodeRunId'],
                     iterationPath=tuple(context['iterationPath']), payload=payload)
