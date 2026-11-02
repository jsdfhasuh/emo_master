import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from emo_master.apps.designer.state.project_store import loadProjectDocument, saveProject
from emo_master.apps.operator_runtime.controller import ProductionRuntime
from emo_master.core.project.delivery_store import DirectoryOwner
from emo_master.core.project.runtime_package import buildRuntimePackage, installRuntimePackage
from tests.runtime.current_production_fixture import currentProductionProject, imageBytes, node, savedPixels
from tests.runtime.production_fixture import waitFor


def runOnce(owner):
    job = owner.start()
    waitFor(lambda: owner.status()['canLoad'])
    record = owner.runtime.jobRepository.get(job)
    assert record.status == 'COMPLETED', (record.errorCode, record.message)
    assert not owner.runtime.jobSupervisor.ownsJobResources(job)
    return job


def testReloadPublishesDiskChangesAndPersistsActualConfiguration(tmp_path):
    path, document = currentProductionProject(tmp_path / 'first')
    data = tmp_path / 'runtime'
    owner = ProductionRuntime(data)
    try:
        owner.load(path)
        runOnce(owner)
        node(document, 'save').params['outputPath'] = 'outputs/changed.png'
        document.production.cycleIntervalMs = 432
        saveProject(path, document.model_dump(mode='json'))
        owner.load(path)
        assert owner.settings.cycleIntervalMs == 432
        assert owner.runtime.projectConfiguration.current['documentRevision'] == 2
        assert owner.runtime.loadedProjectFile == str(path)
        runOnce(owner)
        assert (path.parent / 'outputs/changed.png').exists()
    finally:
        owner.close()
    restored = ProductionRuntime(data)
    try:
        assert not restored.runtime.onlineRecoveryError
        assert restored.runtime.loadedDocument.production.cycleIntervalMs == 432
        assert restored.runtime.loadedProjectFile == str(path)
    finally:
        restored.close()


def testExplicitReloadRefreshesInputBytesWithoutJsonChanges(tmp_path):
    path, _document = currentProductionProject(tmp_path / 'first')
    owner = ProductionRuntime(tmp_path / 'runtime')
    try:
        owner.load(path)
        runOnce(owner)
        assert (savedPixels(path.parent / 'outputs/result.png') == 37).all()
        (path.parent / 'input.png').write_bytes(imageBytes(193))
        owner.load(path)
        runOnce(owner)
        assert (savedPixels(path.parent / 'outputs/result.png') == 193).all()
    finally:
        owner.close()


@pytest.mark.parametrize('sameIdentity', [False, True])
def testSwitchUsesSelectedDirectoryAndReleasesPreviousOwner(tmp_path, sameIdentity):
    first, document = currentProductionProject(tmp_path / 'first')
    second, _document = currentProductionProject(tmp_path / 'second', value=193,
        projectId=document.project.projectId if sameIdentity else None)
    owner = ProductionRuntime(tmp_path / 'runtime')
    try:
        owner.load(first)
        owner.load(second)
        assert owner.runtime.loadedProjectFile == str(second)
        assert owner.projectOwner.root == second.parent
        assert owner.runtime.projectConfiguration.current['sourcePath'] == str(second)
        with DirectoryOwner(first.parent):
            pass
        runOnce(owner)
        assert (savedPixels(second.parent / 'outputs/result.png') == 193).all()
        assert not (first.parent / 'outputs/result.png').exists()
    finally:
        owner.close()


def testFailedManagedSelectionKeepsPreviousProjectAndCanRetry(tmp_path):
    first, _document = currentProductionProject(tmp_path / 'first')
    second, document = currentProductionProject(tmp_path / 'second')
    node(document, 'load').params['imagePath'] = 'not-configured.png'
    saveProject(second, document.model_dump(mode='json'))
    owner = ProductionRuntime(tmp_path / 'runtime')
    try:
        owner.load(first)
        snapshot = owner.runtime.projectConfiguration.snapshot()
        with pytest.raises(ValueError, match='input file not found'):
            owner.load(second)
        assert owner.runtime.projectConfiguration.snapshot() == snapshot
        assert owner.runtime.loadedProjectFile == str(first)
        assert owner.projectOwner.root == first.parent and owner.status()['canStart']
        with DirectoryOwner(second.parent):
            pass
        runOnce(owner)
        node(document, 'load').params['imagePath'] = 'input.png'
        saveProject(second, document.model_dump(mode='json'))
        owner.load(second)
        runOnce(owner)
    finally:
        owner.close()


def testManagedPackageUpdatesDiskAndOnlineStateWithoutTouchingOutputs(tmp_path):
    source, document = currentProductionProject(tmp_path / 'source', declared=True)
    destination = tmp_path / 'installed'
    installRuntimePackage(buildRuntimePackage(source, tmp_path / 'packages'), destination)
    path = destination / 'project.json'
    owner = ProductionRuntime(tmp_path / 'runtime')
    try:
        owner.load(path)
        runOnce(owner)
        history = destination / 'outputs/history.csv'
        history.write_bytes(b'keep history')
        data = imageBytes(193)
        (source.parent / 'input.png').write_bytes(data)
        document.resources.items['input'].sha256 = hashlib.sha256(data).hexdigest()
        document.resources.items['input'].size = len(data)
        document.production.cycleIntervalMs = 432
        saveProject(source, document.model_dump(mode='json'))
        owner.installPackage(buildRuntimePackage(source, tmp_path / 'packages'))
        assert owner.settings.cycleIntervalMs == 432
        assert loadProjectDocument(path).production.cycleIntervalMs == 432
        assert history.read_bytes() == b'keep history'
        runOnce(owner)
        assert (savedPixels(destination / 'outputs/result.png') == 193).all()
    finally:
        owner.close()


def testPackageRejectedByRuntimeRestoresDiskAndKeepsOnlineProject(tmp_path):
    source, document = currentProductionProject(tmp_path / 'source', declared=True)
    destination = tmp_path / 'installed'
    installRuntimePackage(buildRuntimePackage(source, tmp_path / 'packages'), destination)
    path = destination / 'project.json'
    owner = ProductionRuntime(tmp_path / 'runtime')
    try:
        owner.load(path)
        snapshot = owner.runtime.projectConfiguration.snapshot()
        original = path.read_bytes()
        node(document, 'save').params['outputPath'] = str(owner.dataRoot / 'runtime.sqlite3')
        saveProject(source, document.model_dump(mode='json'))
        with pytest.raises(ValueError, match='output overlaps'):
            owner.installPackage(buildRuntimePackage(source, tmp_path / 'packages'))
        assert path.read_bytes() == original
        assert owner.runtime.projectConfiguration.snapshot() == snapshot
        runOnce(owner)
    finally:
        owner.close()


def testRunningManagedProjectRefusesReloadSwitchAndPackageWithoutStopping(tmp_path):
    path, _document = currentProductionProject(tmp_path / 'first', mode='continuous')
    second, _document = currentProductionProject(tmp_path / 'second')
    package = buildRuntimePackage(path, tmp_path / 'packages')
    owner = ProductionRuntime(tmp_path / 'runtime')
    try:
        owner.load(path)
        job = owner.start()
        waitFor(lambda: any(e.eventType == 'workflow.completed' for e in owner.runtime.eventStore.read(job)))
        for operation in (lambda: owner.load(path), lambda: owner.load(second), lambda: owner.installPackage(package)):
            with pytest.raises(ValueError, match='stop the current'):
                operation()
        assert owner.runtime.jobRepository.get(job).status == 'RUNNING'
        assert owner.runtime.projectConfiguration.current['documentRevision'] == 1
        owner.stop()
        owner.load(second)
    finally:
        owner.close()


def testCheckValidatesSelectedDiskFileWithoutChangingOnlineAuthority(tmp_path):
    path, document = currentProductionProject(tmp_path / 'first')
    data = tmp_path / 'runtime'
    owner = ProductionRuntime(data)
    try:
        owner.load(path)
        node(document, 'load').params['imagePath'] = 'not-configured.png'
        saveProject(path, document.model_dump(mode='json'))
        before = owner.runtime.projectConfiguration.snapshot()
        script = Path(__file__).resolve().parents[2] / 'scripts/run_operator.py'
        result = subprocess.run([sys.executable, str(script), str(path), '--check', '--data-root', str(data)],
            capture_output=True, text=True, timeout=30)
        assert result.returncode != 0 and 'input file not found' in result.stderr
        assert owner.runtime.projectConfiguration.snapshot() == before
    finally:
        owner.close()


def testRestoringOnlineProjectDoesNotPublishUnrequestedDiskChanges(tmp_path):
    path, document = currentProductionProject(tmp_path / 'first')
    owner = ProductionRuntime(tmp_path / 'runtime')
    try:
        owner.load(path)
    finally:
        owner.close()
    document.production.cycleIntervalMs = 432
    saveProject(path, document.model_dump(mode='json'))
    owner = ProductionRuntime(tmp_path / 'runtime')
    try:
        owner.load(path, restoreOnline=True)
        assert owner.settings.cycleIntervalMs == 100
        assert owner.runtime.projectConfiguration.current['documentRevision'] == 1
        owner.load(path)
        assert owner.settings.cycleIntervalMs == 432
        assert json.loads(path.read_text())['production']['cycleIntervalMs'] == 432
    finally:
        owner.close()


def testLocalReloadReplacesConnectedSimulatedDeviceAndKeepsExplicitConnection(tmp_path):
    from emo_master.core.project.models import ProjectDocument
    from tests.runtime.test_device_lifecycle import control
    from tests.runtime.test_device_runtime import simulatedProject
    document = ProjectDocument.model_validate(simulatedProject())
    assert document.schemaVersion == '2.9'
    path = tmp_path / 'project/inspection.emoproj'
    saveProject(path, document.model_dump(mode='json'))
    assert loadProjectDocument(path) == document
    owner = ProductionRuntime(tmp_path / 'runtime')
    try:
        owner.load(path)
        manager = owner.runtime.deviceManager
        control(manager, 'connect')
        old = manager.drivers['camera']
        assert old.connected
        document.devices.definitions['camera'].connection['serial'] = 'RELOADED-SIMULATED'
        saveProject(path, document.model_dump(mode='json'))
        owner.load(path)
        assert manager.drivers['camera'] is not old
        assert not old.connected
        waitFor(lambda: manager.lifecycles['camera'].connected)
        assert manager.definitions['camera'].connection['serial'] == 'RELOADED-SIMULATED'
        runOnce(owner)
    finally:
        owner.close()
