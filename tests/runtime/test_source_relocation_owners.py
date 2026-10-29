"""Source relocation fences real management actions and retained cycle owners."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from threading import Event, RLock
from types import SimpleNamespace

import pytest

from emo_master.apps.designer.services.runtime_client import RuntimeClientError
from emo_master.apps.designer.state.project_store import loadProjectDocument, saveProject
from emo_master.apps.runtime.project_config.impact import sourceCycleOwners
from emo_master.apps.runtime.project_config.store import ConfigurationError
from emo_master.apps.runtime.project_config.transition import DeviceTransition
from emo_master.core.project.models import ProjectDocument
from emo_master.core.workflow.compiler import WorkflowCompiler
from tests.runtime.test_device_feedback_closure import tickerRuntime  # noqa: F401
from tests.runtime.test_device_production_outcomes import waitFor
from tests.runtime.test_initial_project_configuration import waitForWrite


@pytest.fixture
def relocationRuntime(tickerRuntime, tmp_path):  # noqa: F811
    service, client = tickerRuntime
    waitFor(lambda: service.deviceManager.lifecycles['ticker'].connected, timeout=5)
    snapshot = client.getOnlineProjectSnapshot()
    document = ProjectDocument.model_validate(snapshot['payload'])
    assert document.schemaVersion == '2.7'
    destination = tmp_path / 'relocated ticker' / 'ticker.emoproj'
    saveProject(destination, document.model_dump(mode='json'))
    reloaded = loadProjectDocument(destination)
    assert reloaded == document
    WorkflowCompiler(service.pluginScanResult.activeOperators,
        deviceTypes=service.pluginScanResult.deviceTypes).compile(reloaded, pluginRootPaths=service.pluginRootPaths)
    return service, client, snapshot, destination


def writeSource(service, client, snapshot, destination, requestId, policy='reject'):
    client.writeProjectConfiguration(snapshot, snapshot['payload'], [], requestId,
        localProjectPath=destination, replacementPolicy=policy)
    return waitForWrite(service, client, requestId)


def assertCommittedSource(service, client, snapshot, destination, receipt):
    assert receipt['state'] == 'COMMITTED', receipt
    assert receipt['documentRevision'] == snapshot['documentRevision'] + 1
    assert receipt['executionRevision'] == snapshot['executionRevision'] + 1
    current = client.getOnlineProjectSnapshot()
    assert current['payload'] == snapshot['payload']
    assert Path(current['sourcePath']) == destination
    assert Path(service.loadedProjectFile) == destination
    assert Path(service.loadedProjectPath) == destination.parent
    persisted = service.projectConfiguration.store.snapshot()
    assert persisted['payload'] == snapshot['payload']
    assert persisted['sourcePath'] == current['sourcePath']
    assert persisted['executionRevision'] == receipt['executionRevision']


@pytest.mark.parametrize('policy', ['reject', 'stopAffected', 'drainAffected'])
def testExecutingDeviceActionBlocksRelocationUntilOwnerRetires(relocationRuntime, monkeypatch, policy):
    service, client, snapshot, destination = relocationRuntime
    manager, configuration = service.deviceManager, service.projectConfiguration
    driver = manager.drivers['ticker']
    execute = driver.execute
    entered, release = Event(), Event()
    persisted = configuration.store.snapshot()
    sourceBytes = Path(snapshot['sourcePath']).read_bytes()

    def heldAction(*args, **kwargs):
        entered.set()
        assert release.wait(10), 'test did not release the simulated ticker action'
        return execute(*args, **kwargs)

    monkeypatch.setattr(driver, 'execute', heldAction)
    runtimeId, states = client.getDeviceStates(service.loadedProjectId)
    with ThreadPoolExecutor(max_workers=1) as pool:
        action = pool.submit(client.executeDeviceAction, service.loadedProjectId, runtimeId,
            'ticker', states['ticker'], 'sample', {})
        try:
            assert entered.wait(5), 'ExecuteDeviceAction did not reach the ticker driver'
            owners = set(manager.maintenanceOwners)
            assert len(owners) == 1 and next(iter(owners)).startswith('device-page-')
            assert sourceCycleOwners(service) == owners
            assert not service.jobRepository.all()
            if policy == 'reject':
                with pytest.raises(RuntimeClientError) as rejected:
                    client.writeProjectConfiguration(snapshot, snapshot['payload'], [], 'busy-check',
                        validate=True, localProjectPath=destination)
                assert rejected.value.code == 'E_CONFIG_REQUIRES_STOP'
            receipt = writeSource(service, client, snapshot, destination, 'busy-write', policy)
            assert receipt['state'] == 'FAILED' and receipt['code'] == 'E_CONFIG_REQUIRES_STOP', receipt
            assert not action.done() and set(manager.maintenanceOwners) == owners
            assert configuration.store.snapshot() == persisted
            assert client.getOnlineProjectSnapshot() == snapshot
            assert service.loadedProjectFile == snapshot['sourcePath']
            assert manager.drivers['ticker'] is driver and manager.lifecycles['ticker'].connected
            assert not driver.stop.is_set()
        finally:
            release.set()
        result = action.result(timeout=5)
        assert isinstance(result['ticks'], int)

    assert not manager.maintenanceOwners and not sourceCycleOwners(service)
    assert not configuration.cycles and not configuration.childCycles
    receipt = writeSource(service, client, snapshot, destination, 'released-write', policy)
    assertCommittedSource(service, client, snapshot, destination, receipt)
    assert manager.drivers['ticker'] is driver and manager.lifecycles['ticker'].connected
    assert Path(snapshot['sourcePath']).read_bytes() == sourceBytes


def testSourceCycleOwnersIncludesChildOnlyOwnersAndExcludesOtherProjects():
    def lease(projectId):
        return dict(snapshot=dict(payload=dict(project=dict(projectId=projectId))))

    configuration = SimpleNamespace(current=lease('current')['snapshot'], cycleLock=RLock(),
        cycles={'root': lease('current'), 'foreign-root': lease('other'), 'mixed': lease('other')},
        childCycles={'child-only': {'child': lease('current')},
            'foreign-child': {'child': lease('other')},
            'mixed': {'child': lease('current')}})
    assert sourceCycleOwners(SimpleNamespace(projectConfiguration=configuration)) == {'root', 'child-only', 'mixed'}


@pytest.mark.parametrize('policy', ['stopAffected', 'drainAffected'])
def testNonJobLeaseRejectsTransitionBeforeStoppingAnyJob(relocationRuntime, monkeypatch, policy):
    service, client, snapshot, destination = relocationRuntime
    configuration = service.projectConfiguration
    root = configuration.beginCycle('diagnostic', snapshot['executionRevision'])
    child = configuration.beginCycle('diagnostic', snapshot['executionRevision'], parentCycleId=root['cycleId'])
    record = SimpleNamespace(jobId='affected-job', projectId=service.loadedProjectId,
        workflowId='main', isTerminal=False)
    stops = []

    def stop(jobId):
        stops.append(jobId)
        return SimpleNamespace(ok=False, message='non-Job lease should reject before stopping this Job')

    try:
        # A synthetic Job isolates ordering; the real ticker/lease authority stays intact.
        with monkeypatch.context() as patch:
            patch.setattr(service.jobRepository, 'all', lambda: [record])
            patch.setattr(service.jobRepository, 'get', lambda jobId: record if jobId == record.jobId else None)
            patch.setattr(service.jobSupervisor, 'ownsJobResources', lambda jobId: jobId == record.jobId)
            patch.setattr(service.jobSupervisor, 'stopJobOutcome', stop)
            with pytest.raises(ConfigurationError) as rejected:
                DeviceTransition(service, deepcopy(snapshot['payload']), Event(), policy, sourcePath=str(destination))
            assert rejected.value.code == 'E_CONFIG_REQUIRES_STOP'
            assert stops == []
        assert sourceCycleOwners(service) == {'diagnostic'}
        assert client.getOnlineProjectSnapshot() == snapshot
        assert not configuration.drainingOwners and not configuration.parkedOwners
    finally:
        configuration.endCycle('diagnostic', child['cycleId'])
        configuration.endCycle('diagnostic', root['cycleId'])


@pytest.mark.parametrize('nested', [False, True], ids=['root', 'root-and-child'])
def testPrecommitRechecksLeaseAcquiredAfterPreparation(relocationRuntime, monkeypatch, nested):
    service, client, snapshot, destination = relocationRuntime
    configuration = service.projectConfiguration
    persisted = configuration.store.snapshot()
    prepare = configuration.prepare
    entered, release = Event(), Event()

    def preparedThenWait(payload, cancelled, **options):
        prepared = prepare(payload, cancelled, **options)
        entered.set()
        assert release.wait(10), 'test did not release prepared configuration'
        return prepared

    monkeypatch.setattr(configuration, 'prepare', preparedThenWait)
    root = child = None
    try:
        client.writeProjectConfiguration(snapshot, snapshot['payload'], [], 'late-lease', localProjectPath=destination)
        assert entered.wait(5), 'candidate preparation did not finish'
        # Direct lease acquisition is an auxiliary counterexample for the final guard,
        # not a claim that ExecuteDeviceAction bypasses the active-writer admission.
        root = configuration.beginCycle('late-owner', snapshot['executionRevision'])
        if nested:
            child = configuration.beginCycle('late-owner', snapshot['executionRevision'], parentCycleId=root['cycleId'])
        release.set()
        receipt = waitForWrite(service, client, 'late-lease')
        assert receipt['state'] == 'FAILED' and receipt['code'] == 'E_CONFIG_REQUIRES_STOP', receipt
        assert configuration.store.snapshot() == persisted
        assert client.getOnlineProjectSnapshot() == snapshot
        assert service.loadedProjectFile == snapshot['sourcePath']
        assert sourceCycleOwners(service) == {'late-owner'}
    finally:
        release.set()
        if child is not None:
            configuration.endCycle('late-owner', child['cycleId'])
        if root is not None:
            configuration.endCycle('late-owner', root['cycleId'])
        if configuration.thread is not None:
            configuration.thread.join(5)
        monkeypatch.setattr(configuration, 'prepare', prepare)

    receipt = writeSource(service, client, snapshot, destination, 'after-lease')
    assertCommittedSource(service, client, snapshot, destination, receipt)
