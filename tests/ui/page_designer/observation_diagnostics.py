"""Bounded metadata for the two-job observation test; no extra product reads."""
import json
from pathlib import Path
import threading
import time

import grpc


def resultState(result):
    return {'identity': result.identity.model_dump(mode='json'), 'status': result.status,
            'terminal': result.executionTerminal,
            'sources': [{'sourceId': source.sourceId, 'state': source.state,
                         'reasonCode': source.reasonCode, 'reason': source.reason,
                         'image': source.image.model_dump(mode='json') if source.image else None}
                        for source in result.sources]}


def clientState(session):
    view = session.readSnapshot()
    return {'jobId': view.jobId, 'connection': view.connection, 'detail': view.detail,
            'generation': view.generation, 'revision': view.revision,
            'readResults': session.readResults, 'resultsAvailable': session._resultsAvailable,
            'stopped': session.stop.is_set(), 'stats': dict(session.stats),
            'errors': list(session.errors), 'records': list(session.records)[-16:],
            'scheduled': list(session._scheduled),
            'wantedImages': None if session._wantedImages is None else list(session._wantedImages),
            'consumers': {key: None if sources is None else list(sources)
                          for key, sources in session._imageConsumers.items()},
            'scopes': {key: dict(result=resultState(scope.result), decoded=list(scope.images),
                                 failures=dict(scope.failures), imageStates=dict(scope.imageStates))
                       for key, scope in view.scopes.items()},
            'loading': {key: resultState(result) for key, result in view.loading.items()},
            'threads': {thread.name: thread.is_alive() for thread in session.threads}}


def observerState(observer):
    readiness = []
    for component, widget in observer.widgets.get(observer.currentPageId, {}).values():
        if component.type != 'image' or not component.bindings:
            continue
        sourceId = next(iter(component.bindings.values()))
        source = observer.config.dataSources.get(sourceId)
        scope = observer.displayed.get(source.resultScopeId) if source else None
        readiness.append({'componentId': component.componentId, 'sourceId': sourceId,
            'sourceExists': source is not None, 'scopeExists': scope is not None,
            'sourceDecoded': scope is not None and sourceId in scope.images,
            'imageNull': widget.image.isNull(), 'widgetKey': widget.key,
            'scopeKey': scope.result.identity.resultKey if scope else None,
            'resultKeyMatches': scope is not None and widget.key == scope.result.identity.resultKey,
            'tooltip': widget.toolTip()})
    return {'page': observer.currentPageId, 'visible': observer.isVisible(),
            'detached': observer.detached, 'designExamples': observer.designExamples,
            'banner': observer.banner.text(), 'status': observer.status.text(),
            'readiness': readiness}


class ObservationEvidence:
    """Test-local read trace, retained even when a transient failure recovers.

    The original read, deadline and exception are unchanged. GUI observations
    are taken by the test's main thread, separately from decoder observations.
    """
    maxReads = 32
    maxStages = 8
    maxBytes = 256 * 1024

    def __init__(self, path, *, platform, jobs):
        self.path = Path(path)
        self.jobs = jobs
        self.lock = threading.RLock()
        self.stage = 'setup'
        self.firstErrorObserved = False
        self.data = {'outcome': 'in_progress', 'qtPlatform': platform,
                     'semantics': 'best-effort, non-atomic; read errors precede caller handling; '
                                  'GUI is the next main-thread observation and may already reflect recovery',
                     'readCount': 0, 'errorCount': 0, 'droppedReads': 0,
                     'firstError': None, 'reads': [], 'stages': [], 'diagnosticErrors': []}

    def install(self, monkeypatch):
        from emo_master.clients.runtime.display_session import DisplaySession
        original = DisplaySession._readImage

        def read(session, result, image, **kwargs):
            if result.identity.jobId not in self.jobs:
                return original(session, result, image, **kwargs)
            error = None
            startedMs, startedNs = int(time.time() * 1000), time.monotonic_ns()
            try:
                return original(session, result, image, **kwargs)
            except Exception as caught:
                error = caught
                raise
            finally:
                endedNs = time.monotonic_ns()
                try:
                    self._read(session, result, image, error, startedMs, startedNs, endedNs)
                except Exception as diagnosticError:
                    self._diagnosticError('read', diagnosticError)

        monkeypatch.setattr(DisplaySession, '_readImage', read)

    def _read(self, session, result, image, error, startedMs, startedNs, endedNs):
        record = {'stage': self.stage, 'startedAtMs': startedMs, 'startedNs': startedNs,
                  'endedNs': endedNs, 'readDecodeMs': (endedNs - startedNs) / 1e6,
                  'identity': result.identity.model_dump(mode='json'),
                  'resultStatus': result.status, 'executionTerminal': result.executionTerminal,
                  'image': image.model_dump(mode='json'),
                  'sources': [{'sourceId': source.sourceId, 'state': source.state,
                               'reasonCode': source.reasonCode}
                              for source in result.sources if source.image == image],
                  'error': None}
        if error is not None:
            record['error'] = {'type': type(error).__name__,
                'code': error.code().name if isinstance(error, grpc.RpcError) else None,
                'details': (error.details() or '')[:2048] if isinstance(error, grpc.RpcError) else str(error)[:2048]}
        first = False
        with self.lock:
            self.data['readCount'] += 1
            if len(self.data['reads']) < self.maxReads:
                self.data['reads'].append(record)
            else:
                self.data['droppedReads'] += 1
            if error is not None:
                self.data['errorCount'] += 1
                if self.data['firstError'] is None:
                    self.data['firstError'] = record
                    first = True
        if first:
            # Preserve the actual first exception before cleanup or successful
            # recovery can erase it. No RPC, pixel access or Qt call is added.
            self._write()

    def observeFirstError(self, preview):
        with self.lock:
            needed = self.data['firstError'] is not None and not self.firstErrorObserved
            if needed:
                self.firstErrorObserved = True
        if needed:
            self.capture('first_error_main_thread', preview)

    def capture(self, stage, preview):
        record = {'stage': stage, 'observedAtMs': int(time.time() * 1000),
                  'observedNs': time.monotonic_ns()}
        session = preview.session
        try:
            if session is not None and session.lock.acquire(blocking=False):
                try:
                    record['client'] = clientState(session)
                    record['client']['records'] = record['client']['records'][-4:]
                    # Freeze dictionaries while the client lock is held.
                    record['client'] = json.loads(json.dumps(record['client'], ensure_ascii=False))
                finally:
                    session.lock.release()
            else:
                record['client'] = {'unavailable': 'no_session_or_lock_busy'}
            record['observer'] = observerState(preview.observer) if preview.observer is not None else None
        except Exception as diagnosticError:
            record['diagnosticError'] = repr(diagnosticError)
            self._diagnosticError('stage', diagnosticError)
        with self.lock:
            if len(self.data['stages']) < self.maxStages:
                self.data['stages'].append(record)

    def finish(self, outcome, stage, *, failedAt=None):
        with self.lock:
            self.data.update(outcome=outcome, finalStage=stage,
                             failedAt=failedAt or (stage if outcome == 'failed' else None))
        self._write()

    def _write(self):
        try:
            with self.lock:
                report = dict(self.data, expectedJobs=list(self.jobs))
                content = json.dumps(report, ensure_ascii=False, indent=2)
                if len(content.encode('utf-8')) + 1 > self.maxBytes:
                    # Keep the first exact error and totals even if verbose
                    # post-read snapshots exceed this test artifact's budget.
                    report.update(reads=[], stages=[], outputTruncated=True)
                    content = json.dumps(report, ensure_ascii=False, indent=2)
                if len(content.encode('utf-8')) + 1 > self.maxBytes:
                    raise ValueError('observation evidence exceeds 256 KiB')
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.write_text(content + '\n', encoding='utf-8')
        except Exception as diagnosticError:
            self._diagnosticError('write', diagnosticError)

    def _diagnosticError(self, stage, error):
        detail = {'stage': stage, 'error': repr(error)[:2048]}
        with self.lock:
            if len(self.data['diagnosticErrors']) < 8:
                self.data['diagnosticErrors'].append(detail)
        print('TWO_JOB_OBSERVATION DIAGNOSTIC_ERROR ' + json.dumps(detail), flush=True)


def printObservationFailure(preview, runtime, jobs, stage):
    """Best-effort sections at different times; never read image bytes or issue RPCs."""
    report = {'stage': stage, 'observedNs': time.monotonic_ns(), 'jobs': list(jobs),
              'semantics': 'best-effort sections, not an atomic end-to-end snapshot',
              'threadCount': threading.active_count()}

    def section(name, read, lock=None):
        acquired = False
        try:
            if lock is not None:
                acquired = lock.acquire(timeout=.25)
                if not acquired:
                    raise TimeoutError('diagnostic lock acquisition exceeded 250 ms')
            # Freeze metadata while its lock is held; one broken section must
            # not discard the remaining evidence or mask the original failure.
            report[name] = json.loads(json.dumps(read(), ensure_ascii=False))
        except Exception as error:
            report[name] = {'diagnosticError': repr(error)}
        finally:
            if acquired:
                lock.release()

    section('preview', lambda: {'error': str(preview.error),
                               'selectedJob': preview.selectedJob.job_id if preview.selectedJob else None})
    session = preview.session
    if session is not None:
        section('client', lambda: clientState(session), session.lock)
    observer = preview.observer
    if observer is not None:
        section('observer', lambda: observerState(observer))
    hub = preview.hub
    if hub is not None:
        section('hub', lambda: {'windows': len(hub.windows), 'timerActive': hub.timer.isActive(),
                               'retired': hub.lifecycle['retired'], 'conversions': hub.conversions})
    owner = runtime._presentationOwner
    if owner is not None:
        section('runtime', lambda: {
            'latest': [resultState(result) for result in owner.store.latest.values()],
            'openKeys': list(owner.store.open), 'pendingKeys': list(owner.pending),
            'diagnostics': list(owner.diagnostics)[-16:],
            'presentationErrors': list(runtime.jobSupervisor.presentationErrors)[-16:]}, owner.store.lock)
        assets = owner.assets
        section('assets', lambda: {
            'items': {key: {field: item[field] for field in ('job', 'owner', 'size', 'sha')}
                      for key, item in assets.items.items()},
            'readers': dict(assets.readers), 'leaseHandles': len(assets.leases)}, assets.lock)
        exporter = owner.exporter
        if exporter is not None:
            # ownershipSnapshot uses nonblocking slot locks and marks busy slots.
            section('exports', lambda: {'stats': dict(exporter.stats), 'errors': list(exporter.errors),
                'ownership': {job: exporter.ownershipSnapshot(job) for job in jobs}})
    print('TWO_JOB_OBSERVATION DIAGNOSTICS ' + json.dumps(report, ensure_ascii=False), flush=True)
