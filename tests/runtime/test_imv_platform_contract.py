"""Software simulation must not broaden the production IMV platform contract."""
import json
from types import SimpleNamespace

import pytest

from emo_master.apps.runtime.devices import drivers, manager as managerModule
from emo_master.apps.runtime.devices.executor import DeviceError
from emo_master.apps.runtime.devices.manager import DeviceManager
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from emo_master.core.plugin.registry import PluginRegistry
from emo_master.core.project.models import ProjectDocument
from tests.plugins.test_camera_configuration import ConfigurationApi
from tests.runtime.imv_test_support import IMV_TYPE, SimulatedImvRuntimeService, SimulatedManagedImv, useSimulatedImv
from tests.runtime.test_device_production_outcomes import waitFor
from tests.runtime.test_managed_imv import ROOT, cameraProject


@pytest.mark.parametrize("system", ["Linux", "Darwin"])
def testProductionImvRejectsUnsupportedPlatformBeforeResolvingDriver(monkeypatch, system):
    types, rejected = PluginRegistry("0.6.1").scanDeviceTypes([ROOT])
    assert not rejected and types[IMV_TYPE].platforms == ["windows"]
    monkeypatch.setattr(managerModule, "platform", SimpleNamespace(system=lambda: system))
    monkeypatch.setattr(drivers, "resolveDriver", lambda _: pytest.fail("unsupported driver must not resolve"))
    manager = DeviceManager(types)
    try:
        with pytest.raises(DeviceError) as error:
            manager.load(ProjectDocument.model_validate(cameraProject()))
        assert error.value.code == "E_DEVICE_PLATFORM"
        assert not manager.definitions and not manager.drivers and not manager.executors
    finally:
        manager.close()


@pytest.mark.parametrize("system", ["Linux", "Darwin"])
def testProductionRuntimeLoadPreservesPlatformRejection(tmp_path, monkeypatch, system):
    path = tmp_path / "camera.emoproj"
    path.write_text(json.dumps(cameraProject()), encoding="utf-8")
    monkeypatch.setattr(managerModule, "platform", SimpleNamespace(system=lambda: system))
    service = RuntimeService(dbPath=tmp_path / "runtime.db", workspaceRoot=tmp_path / "jobs")
    try:
        reply = service.LoadProject(pb.LoadProjectRequest(project_path=str(path)), None)
        assert not reply.ok and reply.status == "FAILED"
        assert "does not support this Runtime platform" in reply.message
        assert not service.deviceManager.drivers and service.loadedDocument is None
    finally:
        service.close()


def testSimulationInjectsFakeBeforeAutomaticConnectAndLeavesRegistryUnchanged(tmp_path):
    payload = cameraProject()
    payload["devices"]["definitions"]["camera"]["policies"]["connect"] = "on_project_open"
    path = tmp_path / "camera.emoproj"
    path.write_text(json.dumps(payload), encoding="utf-8")
    service = RuntimeService(dbPath=tmp_path / "runtime.db", workspaceRoot=tmp_path / "jobs")
    try:
        manager = service.deviceManager
        productionTypes = service.pluginScanResult.deviceTypes
        before = productionTypes[IMV_TYPE].model_dump()
        useSimulatedImv(manager)
        assert manager.types is not productionTypes
        assert productionTypes[IMV_TYPE].model_dump() == before
        assert before["platforms"] == ["windows"]
        assert before["driverEntry"] == "emo_master.apps.runtime.devices.imv_host:ManagedImv"
        excluded = {"platforms", "driverEntry", "displayName"}
        assert manager.types[IMV_TYPE].model_dump(exclude=excluded) == productionTypes[IMV_TYPE].model_dump(exclude=excluded)
        reply = service.LoadProject(pb.LoadProjectRequest(project_path=str(path)), None)
        assert reply.ok, reply.message
        driver = manager.drivers["camera"]
        assert isinstance(driver, SimulatedManagedImv) and driver.apiFactory is ConfigurationApi
        waitFor(lambda: manager.lifecycles["camera"].connected)
        assert driver.process.is_alive() and driver.sequence == 1
        assert not manager.frames.frames and not manager.frames.reservations
        fresh, rejected = PluginRegistry("0.6.1").scanDeviceTypes([ROOT])
        assert not rejected and fresh[IMV_TYPE].model_dump() == before
    finally:
        service.close()
    restored = SimulatedImvRuntimeService(dbPath=tmp_path / "runtime.db", workspaceRoot=tmp_path / "jobs")
    try:
        assert not restored.onlineRecoveryError
        assert restored.loadedProjectId == payload["project"]["projectId"]
        driver = restored.deviceManager.drivers["camera"]
        assert isinstance(driver, SimulatedManagedImv) and driver.apiFactory is ConfigurationApi
        waitFor(lambda: restored.deviceManager.lifecycles["camera"].connected)
        assert driver.process.is_alive() and driver.sequence == 1
        assert restored.pluginScanResult.deviceTypes[IMV_TYPE].model_dump() == before
    finally:
        restored.close()
