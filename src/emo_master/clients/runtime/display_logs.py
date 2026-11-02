"""Bounded client diagnostics, drained by the owning UI without Qt callbacks."""
from collections import OrderedDict, deque
from dataclasses import dataclass
from threading import RLock
import time


@dataclass
class _Issue:
    code: str
    level: str
    message: str
    context: dict
    started: float
    logged: float
    count: int = 1
    reported: int = 0
    healthySince: float | None = None


class DisplayLog:
    def __init__(self, *, clock=time.monotonic, interval=30.0, recovery=1.0):
        self.clock, self.interval, self.recovery = clock, interval, recovery
        self.lock = RLock()
        self.issues = OrderedDict()
        self.events = deque(maxlen=128)
        self.dropped = 0

    def failed(self, key, code, level, message, context):
        with self.lock:
            now = self.clock()
            previous = self.issues.get(key)
            changed = previous is None or previous.code != code
            if changed:
                if previous is not None and previous.count > previous.reported:
                    self._emit(previous)
                previous = _Issue(code, level, message, context, now, now)
                self.issues[key] = previous
            else:
                previous.count += 1
                previous.context, previous.message = context, message
            previous.healthySince = None
            self.issues.move_to_end(key)
            if changed or now - previous.logged >= self.interval:
                previous.logged = now
                self._emit(previous)
            while len(self.issues) > 256:
                _, retired = self.issues.popitem(last=False)
                if retired.count > retired.reported:
                    self._emit(retired)

    def healthy(self, key, context):
        with self.lock:
            previous = self.issues.get(key)
            if previous is None:
                return
            previous.context = context
            if previous.healthySince is None:
                previous.healthySince = self.clock()
            if self.clock() - previous.healthySince >= self.recovery:
                self._emit(previous, recovered=True)
                del self.issues[key]

    def finish(self):
        with self.lock:
            for issue in self.issues.values():
                if issue.healthySince is not None:
                    self._emit(issue, recovered=True)
                elif issue.count > issue.reported:
                    self._emit(issue)
            self.issues.clear()

    def _emit(self, issue, recovered=False):
        context = issue.context
        payload = dict(context.get('payload', {}), occurrences=issue.count,
                       occurrencesSinceLastLog=issue.count - issue.reported,
                       durationMs=int((self.clock() - issue.started) * 1000))
        issue.reported = issue.count
        if recovered:
            payload['previousCode'] = issue.code
        event = dict(context, timestampMs=int(time.time() * 1000),
                     eventType='presentation.client.recovered' if recovered else 'presentation.client.failed',
                     code='DISPLAY_CLIENT_RECOVERED' if recovered else issue.code,
                     level='INFO' if recovered else issue.level,
                     message=('页面客户端已恢复；此前：' if recovered else '') + issue.message,
                     payload=payload)
        if len(self.events) == self.events.maxlen:
            self.dropped += 1
        self.events.append(event)

    def take(self):
        with self.lock:
            events = list(self.events)
            self.events.clear()
            if self.dropped:
                events.insert(0, dict(timestampMs=int(time.time() * 1000), level='WARN',
                    eventType='presentation.client.log_overflow', code='DISPLAY_LOG_DROPPED',
                    message=f'页面客户端日志队列已满，合并丢弃 {self.dropped} 条较早记录',
                    payload={'dropped': self.dropped}))
                self.dropped = 0
            return events
