"""Failure-only metadata for the two-job observation test, before owner cleanup."""
import json
import threading
import time


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
