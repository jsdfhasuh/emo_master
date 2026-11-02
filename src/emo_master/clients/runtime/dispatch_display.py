"""Read-only viewing of one admitted producer/consumer pair, never execution ownership."""
from threading import RLock
from types import MappingProxyType

from emo_master.clients.runtime.display_session import DisplaySession
from emo_master.clients.runtime.connection import connectionProfile
from emo_master.clients.runtime.pins import PinView
from emo_master.clients.runtime.view_state import SessionView
from emo_master.core.presentation.capture_limits import normalCaptureLimits
from emo_master.core.presentation.coverage import DispatchCoverage


class DispatchDisplaySession:
    imageDemand = True

    def __init__(self, address, producer, consumer, presentation):
        self.connectionProfile = connectionProfile(address)
        self.coverage = DispatchCoverage.fromMetadata(producer, consumer)
        normalCaptureLimits(presentation)
        self.lock = RLock()
        self.sessions = {}
        self._pinStore = None
        self.generation = 0
        self._epochs = None
        try:
            for item in (producer, consumer):
                self.sessions[item.job_id] = DisplaySession(self.connectionProfile, item.job_id, imageDemand=True,
                    expectedRuntimeInstanceId=item.runtime_instance_id, projectId=item.project_id,
                    readResults=bool(item.capture_enabled and not item.resources_released),
                    pinByteLimit=8 * 1024 * 1024)
        except BaseException:
            self.close()
            raise

    def readSnapshot(self):
        with self.lock:
            views = [self.sessions[member.jobId].readSnapshot() for member in self.coverage.members]
            epochs = tuple(view.generation for view in views)
            if epochs != self._epochs:
                self.generation += 1
                self._epochs = epochs
            fields = {key: {} for key in ('scopes', 'loading', 'started', 'expiredScopes')}
            errors, details = {}, []
            for member, view in zip(self.coverage.members, views):
                for key in fields:
                    fields[key].update({scope: value for scope, value in getattr(view, key).items()
                                        if scope in member.scopes})
                if view.connection != 'CONNECTED':
                    error = view.connection + ': ' + view.detail
                    errors.update({scope: error for scope in member.scopes})
                    details.append(member.jobId[:8] + ': ' + error)
            connection = 'CONNECTED' if any(view.connection == 'CONNECTED' for view in views) else views[0].connection
            return SessionView(sum(view.revision for view in views), self.generation,
                views[0].runtimeInstanceId, views[-1].jobId, connection, '; '.join(details),
                **{key: MappingProxyType(value) for key, value in fields.items()},
                job=views[-1].job, scopeErrors=MappingProxyType(errors))

    def setImageDemand(self, consumerId, sourceIds):
        requested = None if sourceIds is None else set(sourceIds)
        for member in self.coverage.members:
            self.sessions[member.jobId].setImageDemand(consumerId,
                set(member.sources) if requested is None else requested & member.sources.keys())

    def removeImageDemand(self, consumerId):
        for session in self.sessions.values():
            session.removeImageDemand(consumerId)

    def takeLogEvents(self):
        return [event for session in self.sessions.values() for event in session.takeLogEvents()]

    def reportImageIssue(self, result, sourceId, *args, **kwargs):
        self.sessions[result.identity.jobId].reportImageIssue(result, sourceId, *args, **kwargs)

    def pins(self):
        if self._pinStore is None:
            self._pinStore = DispatchPins(self)
        return self._pinStore

    def close(self):
        errors = []
        for session in self.sessions.values():
            try:
                session.close()
            except Exception as error:
                errors.append(str(error))
        if errors:
            raise RuntimeError('; '.join(errors))


class DispatchPins:
    """Route finite pins to their original decoder; at most two tickets / 16 MiB."""
    def __init__(self, session):
        self.session = session
        self.routes = {}

    def acquire(self, scope, generation, ttlMs=30000, *, sourceIds=None):
        view = self.session.readSnapshot()
        if generation != view.generation or not self.session.coverage.matches(scope.result.identity):
            raise ValueError('capture identity changed')
        self.routes = {key: store for key, store in self.routes.items() if store.read(key).state != 'EXPIRED'}
        if len(self.routes) >= 2:
            raise ValueError('PIN_BUDGET')
        child = self.session.sessions[scope.result.identity.jobId]
        store = child.pins()
        ticket = store.acquire(scope, child.readSnapshot().generation, ttlMs, sourceIds=sourceIds)
        self.routes[ticket] = store
        return ticket

    def read(self, ticket):
        store = self.routes.get(ticket)
        return store.read(ticket) if store else PinView('EXPIRED', None, 0, 'RESOURCE_EXPIRED')

    def release(self, ticket):
        store = self.routes.pop(ticket, None)
        if store:
            store.release(ticket)

    def setActive(self, ticket, active):
        store = self.routes.get(ticket)
        if store:
            store.setActive(ticket, active)

    def bytesHeld(self):
        return sum(child._pinStore.bytesHeld() for child in self.session.sessions.values() if child._pinStore)
