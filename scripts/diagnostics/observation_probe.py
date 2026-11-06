"""Read-only diagnostics for the exact two-job observation regression.

Run via pytest -p observation_probe with this directory on PYTHONPATH.
Only wraps the existing wait helper, without modifying its timeout or predicate.
Records after image readiness returns or raises, before cleanup removes evidence.
"""
import dataclasses
import inspect
import json
import os
from pathlib import Path
import time
import platform
import sys
import threading
from contextlib import contextmanager


@contextmanager
def observed_lock(lock):
    acquired = lock.acquire(timeout=0.25)
    if not acquired:
        raise TimeoutError("diagnostic lock acquisition exceeded 250 ms")
    try:
        yield
    finally:
        lock.release()

OUTPUT = Path(os.environ.get('EMO_OBSERVATION_PROBE_OUTPUT', 'observation-probe.jsonl'))


def value(obj):
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if hasattr(obj, 'model_dump'):
        return obj.model_dump(mode='json')
    if dataclasses.is_dataclass(obj):
        return {f.name: value(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, dict) or hasattr(obj, 'items'):
        return {str(k): value(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set, frozenset)):
        return [value(v) for v in obj]
    if hasattr(obj, 'shape') and hasattr(obj, 'dtype'):
        return {'shape': list(obj.shape), 'dtype': str(obj.dtype), 'bytes': obj.nbytes}
    return str(obj)


def snapshot(frame, outcome):
    local = frame.f_locals
    preview, runtime = local['preview'], local['runtime']
    record = {'stage': local.get('stage'), 'outcome': outcome, 'observedNs': time.monotonic_ns(),
              'jobs': list(local.get('jobs', ())), 'previewError': value(preview.error),
              'selectedJob': str(preview.selectedJob), 'readiness': []}
    try:
        record['environment'] = {'platform': platform.platform(), 'python': sys.version,
                                 'pid': os.getpid(), 'threadCount': threading.active_count()}
        session = preview.session
        if session is not None:
            with observed_lock(session.lock):
                view = session.readSnapshot()
                record['session'] = value(view)
                record['client'] = {'stats': dict(session.stats), 'errors': list(session.errors),
                                    'records': list(session.records), 'pendingSize': session.pending.qsize(),
                                    'scheduled': value(session._scheduled), 'wantedImages': value(session._wantedImages),
                                    'consumers': value(session._imageConsumers), 'seen': list(session.seen),
                                    'readResults': session.readResults, 'resultsAvailable': session._resultsAvailable,
                                    'compatible': session.compatible, 'stopped': session.stop.is_set()}
        observer = preview.observer
        if observer is not None:
            record['observer'] = {'visible': observer.isVisible(), 'detached': observer.detached,
                                  'designExamples': observer.designExamples, 'page': observer.currentPageId,
                                  'imageSources': value(observer.imageSources()),
                                  'banner': observer.banner.text(), 'status': observer.status.text()}
            for component, widget in observer.widgets.get(observer.currentPageId, {}).values():
                if component.type != 'image' or not component.bindings:
                    continue
                source_id = next(iter(component.bindings.values()))
                source = observer.config.dataSources.get(source_id)
                scope = observer.displayed.get(source.resultScopeId) if source else None
                record['readiness'].append({'componentId': component.componentId, 'sourceId': source_id,
                    'sourceExists': source is not None, 'scopeExists': scope is not None,
                    'sourceDecoded': scope is not None and source_id in scope.images,
                    'imageNull': widget.image.isNull(), 'widgetKey': widget.key,
                    'scopeKey': scope.result.identity.resultKey if scope else None,
                    'resultKeyMatches': scope is not None and widget.key == scope.result.identity.resultKey,
                    'tooltip': widget.toolTip()})
        record['boundImageCount'] = len(record['readiness'])
        record['allReady'] = bool(record['readiness']) and all(
            item['sourceExists'] and item['scopeExists'] and item['sourceDecoded']
            and not item['imageNull'] and item['resultKeyMatches'] for item in record['readiness'])
        hub = preview.hub
        if hub is not None:
            record['hub'] = {'windows': len(hub.windows), 'timerActive': hub.timer.isActive(),
                             'retired': hub.lifecycle['retired'], 'conversions': hub.conversions,
                             'cacheBytes': hub.cacheBytes, 'lastToken': value(hub.lastToken)}
        owner = runtime._presentationOwner
        if owner is not None:
            with observed_lock(owner.store.lock):
                record['runtime'] = {'diagnostics': list(owner.diagnostics),
                    'latest': [value(r) for r in owner.store.latest.values()],
                    'pending': value(owner.pending), 'open': value(owner.store.open),
                    'high': value(owner.store.high), 'expired': value(owner.store.expired),
                    'terminal': value(owner.terminalStates), 'presentationErrors': list(runtime.jobSupervisor.presentationErrors),
                    'jobs': [value(runtime.jobRepository.get(j)) for j in record['jobs']],
                    'readers': {j: {'alive': reader.is_alive(), 'name': reader.name}
                                for j, reader in owner.readers.items()},
                    'ownerState': {'closed': owner.closed, 'closing': owner.closing,
                                   'stopped': owner.stop.is_set(), 'monitorAlive': owner.monitor.is_alive(),
                                   'maxConcurrentJobs': runtime.jobSupervisor.maxConcurrentJobs},
                    'jobSlots': {k: [{'index': s['index'], 'lane': s.get('lane')} for s in v['slots']]
                                 for k, v in owner.jobs.items()}}
            with observed_lock(owner.assets.lock):
                record['assets'] = {'items': value(owner.assets.items), 'leases': value(owner.assets.leases),
                                    'readers': value(owner.assets.readers), 'retained': value(owner.assets.retained)}
            if owner.exporter is not None:
                record['exports'] = {'stats': dict(owner.exporter.stats), 'errors': list(owner.exporter.errors),
                                      'diagnostics': list(owner.exporter.diagnostics),
                                      'ownership': [owner.exporter.ownershipSnapshot(j) for j in record['jobs']]}
    except Exception as error:
        record['diagnosticError'] = repr(error)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT.open('a', encoding='utf-8') as out:
        out.write(json.dumps(value(record), ensure_ascii=False) + '\n')



def safe_snapshot(frame, outcome):
    try:
        snapshot(frame, outcome)
    except Exception as error:
        print(f'OBSERVATION_PROBE_ERROR {error!r}', flush=True)


def pytest_runtest_setup(item):
    if item.module.__name__.rsplit('.', 1)[-1] != 'test_two_job_observation':
        return
    original = item.module.waitFor
    def traced_wait(predicate, *args, **kwargs):
        frame = inspect.currentframe().f_back
        while frame is not None and not ('preview' in frame.f_locals and 'runtime' in frame.f_locals):
            frame = frame.f_back
        stage = frame.f_locals.get('stage', '') if frame is not None else ''
        try:
            returned = original(predicate, *args, **kwargs)
        except BaseException:
            if frame is not None:
                safe_snapshot(frame, 'failed')
            raise
        if frame is not None and stage.endswith('_image'):
            safe_snapshot(frame, 'passed')
        return returned
    item._observation_probe_original_wait = original
    item.module.waitFor = traced_wait


def pytest_runtest_teardown(item):
    original = getattr(item, '_observation_probe_original_wait', None)
    if original is not None:
        item.module.waitFor = original
