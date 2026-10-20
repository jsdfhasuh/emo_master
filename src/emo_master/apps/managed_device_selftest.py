"""Explicit source/frozen spawn probe; never constructs a physical-device project."""
from copy import deepcopy
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import time
from typing import Any


def _probeUnavailableApi():
    from emo_master.apps.runtime.devices.executor import DeviceError
    raise DeviceError("E_SELFTEST_HOST_PROBE", str(os.getpid()), unhealthy=True)


class _NetworkProbeApi:
    def __init__(self):
        self.row = dict(cameraKey='SELFTEST-NO-HARDWARE', cameraType=0, macAddress='00:00:00:00:00:01',
            ipAddress='192.0.2.10', subnetMask='255.255.255.0', defaultGateWay='0.0.0.0', interfaceIp='192.0.2.1')

    def version(self):
        return 'injected-no-sdk'

    def enumerate_device_info(self):
        return [deepcopy(self.row)]

    def create_handle(self, mode, key):
        if mode != 1 or key != self.row['cameraKey']:
            raise ValueError('invalid synthetic camera selector')
        return 0, 'synthetic-handle'

    def force_ip_address(self, handle, address, mask, gateway):
        self.row.update(ipAddress=address, subnetMask=mask, defaultGateWay=gateway)
        return 0

    def destroy_handle(self, handle):
        return 0


def _leaseProbeChild(pipe, resource):
    try:
        pipe.send((os.getpid(), Path(resource).read_bytes()))
        if not pipe.poll(30) or pipe.recv() != 'release':
            raise RuntimeError('resource lease probe release timed out')
    finally:
        pipe.close()


def _checkNetworkAndResourceLease(root):
    import multiprocessing
    from emo_master.apps.runtime.devices.network import runNetworkHost
    from emo_master.apps.runtime.project_config.resource_lease import ResourceLease, retirementLease, runWithResourceLease
    discovery = runNetworkHost('discover', {}, lambda: False, apiFactory=_NetworkProbeApi)
    row = discovery['devices'][0]
    result = runNetworkHost('force_ip', dict(row, before=row, ipAddress='192.0.2.20'),
                            lambda: False, apiFactory=_NetworkProbeApi)
    if result['after']['ipAddress'] != '192.0.2.20':
        raise RuntimeError('synthetic network Host did not confirm readback')
    sentinel, resource = root / 'selftest.lease', root / 'lease-probe.bin'
    resource.write_bytes(b'frozen-resource-lease')
    lease = ResourceLease(sentinel, create=True)
    context = multiprocessing.get_context('spawn')
    parent, child = context.Pipe()
    process = context.Process(target=runWithResourceLease, args=(str(sentinel), _leaseProbeChild, (child, str(resource))))
    try:
        process.start()
        child.close()
        if not parent.poll(30):
            raise RuntimeError('resource lease child did not start')
        pid, data = parent.recv()
        if data != resource.read_bytes() or pid == os.getpid():
            raise RuntimeError('resource consumer was not isolated')
        lease.close()
        with retirementLease(sentinel) as retired:
            if retired:
                raise RuntimeError('live child resource lease was ignored')
        parent.send('release')
        process.join(30)
        if process.exitcode != 0:
            raise RuntimeError('resource lease consumer did not exit cleanly')
        with retirementLease(sentinel) as retired:
            if not retired:
                raise RuntimeError('exited child still blocks resource retirement')
        try:
            late = ResourceLease(sentinel)
        except OSError:
            pass
        else:
            late.close()
            raise RuntimeError('late resource consumer bypassed retirement')
        return dict(networkHost='injected-no-sdk', sdkLoaded=False, hardwareAccessed=False,
                    leaseConsumerPid=pid, liveConsumerProtected=True, lateConsumerRejected=True)
    finally:
        lease.close()
        parent.close()
        child.close()
        if process.pid:
            if process.is_alive():
                process.terminate()
                process.join(5)
            if process.is_alive():
                raise RuntimeError('resource lease probe process did not retire')
            process.close()


def _checkHostFailureRetirement(types):
    from emo_master.apps.runtime.devices.executor import DeviceError
    from emo_master.apps.runtime.devices.imv_host import ManagedImv
    from emo_master.core.project.devices import DeviceDefinition
    declaration = types["device.camera.huaray_imv"]
    definition = declaration.validateDefinition(DeviceDefinition(name="No SDK Host probe",
        deviceTypeId=declaration.deviceTypeId, driverVersion=declaration.version,
        connection={"cameraKey": "SELF-TEST-NO-HARDWARE", "maxFrameBytes": 1024}))
    driver = ManagedImv(definition, apiFactory=_probeUnavailableApi)
    try:
        try:
            driver.execute("capture", definition.defaults, lambda: False, time.monotonic() + 15)
        except DeviceError as error:
            if error.code != "E_SELFTEST_HOST_PROBE":
                raise
            childPid = int(str(error))
        else:
            raise RuntimeError("Host probe unexpectedly acquired a frame")
        if childPid == os.getpid() or driver.process is not None or driver.memory is not None or not driver.unhealthy:
            raise RuntimeError("Host probe failed isolation or native resource retirement")
        return dict(pid=childPid, injectedFailure="E_SELFTEST_HOST_PROBE", quarantined=True,
                    processRetired=True, stagingRetired=True, sdkLoaded=False, hardwareAccessed=False)
    finally:
        driver.close()


def checkManagedRuntime():
    from emo_master.apps.designer.state.workflow_store import WorkflowStore
    from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as _pb
    from emo_master.apps.runtime.grpc_server.service import RuntimeService
    from emo_master.core.project.migration import migrateProjectPayload

    pb: Any = _pb
    payload: dict[str, Any] = migrateProjectPayload(WorkflowStore().toPayload(), enableDevices=True)
    payload["runtime"]["maxConcurrentJobs"] = 4
    payload["devices"]["definitions"]["camera"] = dict(name="Package self-test",
        deviceTypeId="device.simulated.camera", driverVersion="1.0.0",
        connection={"serial": "PACKAGE-SELF-TEST"}, defaults=dict(width=7, height=5, exposureUs=8000, gain=0))
    workflow = payload["workflows"]["main"]
    workflow["outputs"] = {"image": "image"}
    workflow["nodes"] = [dict(nodeId="input", kind="workflow_input"),
        dict(nodeId="capture", operatorId="vision.io.simulated_camera", deviceBindings={"camera": {"deviceId": "camera"}}),
        dict(nodeId="output", kind="workflow_output")]
    workflow["edges"] = [dict(fromNode="capture", fromPort="image", toNode="output", toPort="image")]
    with TemporaryDirectory(prefix="emo-managed-runtime-self-test-") as directory:
        root = Path(directory).resolve()
        path = root / "project.emoproj"
        path.write_text(json.dumps(payload), encoding="utf-8")
        service: Any = RuntimeService(dbPath=root / "runtime.db", workspaceRoot=root / "jobs")
        try:
            loaded = service.LoadProject(pb.LoadProjectRequest(project_path=str(path)), None)
            if not loaded.ok:
                raise RuntimeError(loaded.message)
            started = [service.StartJob(pb.StartJobRequest(project_id=service.loadedProjectId, workflow_id="main"), None)
                       for _ in range(2)]
            if not all(item.ok for item in started):
                raise RuntimeError("managed self-test Job admission failed: " + str(started))
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                statuses = [service.GetJobStatus(pb.GetJobStatusRequest(job_id=item.job_id), None) for item in started]
                if all(item.status in {"COMPLETED", "FAILED", "ABORTED"} for item in statuses):
                    break
                time.sleep(.02)
            if any(item.status != "COMPLETED" for item in statuses):
                raise RuntimeError("managed self-test Job failed or timed out: " + str(statuses))
            pids = {item.pid for item in statuses}
            if len(pids) != 2 or 0 in pids or os.getpid() in pids:
                raise RuntimeError("managed self-test did not use two independent spawned workers")
            manager = service.deviceManager
            deadline = time.monotonic() + 10
            while manager.workers and time.monotonic() < deadline:
                service.jobSupervisor.finishRetirements()
                time.sleep(.02)
            if manager.workers or manager.frames.frames or manager.frames.reservations:
                raise RuntimeError("managed workers or original frames were not retired")
            if manager.drivers["camera"].sequence != 2:
                raise RuntimeError("managed workers did not share the same Runtime adapter")
            dispatch = _checkDispatch(service, pb)
            executor, operation = manager.beginControl("camera", manager.executors["camera"].generation, "disconnect")
            executor.wait(operation)
            coordinator = service.projectConfiguration
            snapshot = coordinator.snapshot()
            candidate = deepcopy(snapshot["payload"])
            candidate["devices"]["definitions"]["camera"]["connection"]["serial"] = "PACKAGE-REPLACED"
            coordinator.start(dict(requestId="self-test-replace", expectedRuntimeInstanceId=service.runtimeInstanceId,
                expectedRevision=snapshot["documentRevision"], localPayload=candidate,
                selectedPaths=["/devices/definitions/camera/connection/serial"]))
            coordinator.thread.join(10)
            receipt = coordinator.status("self-test-replace")
            if coordinator.thread.is_alive() or receipt["state"] != "COMMITTED" or coordinator.publicationError:
                raise RuntimeError("managed replacement did not commit cleanly: " + str(receipt))
            if manager.drivers["camera"].connected or manager.replacement is not None:
                raise RuntimeError("configuration replacement performed an unexpected connection or retained preparation")
            result = dict(workerPids=sorted(pids), acquisitions=2, originalFramesRetired=True,
                replacementRevision=receipt["documentRevision"], hardwareAccessed=False,
                frozen=bool(getattr(sys, "frozen", False)),
                scope="synthetic Runtime, two spawned Jobs, shared original retirement, prepared adapter replacement")
            result["dispatch"] = dispatch
            result["hostFailureProbe"] = _checkHostFailureRetirement(service.pluginScanResult.deviceTypes)
            result["networkAndResourceLease"] = _checkNetworkAndResourceLease(root)
            result["resourceTransfer"] = _checkResourceTransfer(service, root)
        finally:
            service.close()
        result["runtimeClosed"] = service._closed
        restored = RuntimeService(dbPath=root / "runtime.db", workspaceRoot=root / "jobs")
        try:
            if restored.onlineRecoveryError or restored.projectConfiguration.snapshot()["documentRevision"] != 4:
                raise RuntimeError("uploaded configuration did not recover after Runtime restart")
            result["resourceTransfer"]["recoveredAfterRestart"] = True
            _checkStaticAssets(restored.loadedDocument)
            result["resourceTransfer"]["staticAssetsRecovered"] = True
        finally:
            restored.close()
        return result


def _checkResourceTransfer(service, root):
    import hashlib
    import cv2
    import numpy as np
    from emo_master.apps.designer.services.runtime_client import RuntimeClient
    from emo_master.core.project.configuration import projectChanges
    local = root / "designer-only"
    local.mkdir()
    source = local / "package-input.png"
    source.write_bytes(cv2.imencode(".png", np.zeros((5, 7, 3), dtype=np.uint8))[1].tobytes())
    client = RuntimeClient(service)
    snapshot = client.getOnlineProjectSnapshot()
    candidate = deepcopy(snapshot["payload"])
    candidate["resources"]["items"]["package-input"] = dict(path=source.name, size=source.stat().st_size,
        sha256=hashlib.sha256(source.read_bytes()).hexdigest(), purpose="input_image")
    candidate["resources"]["parameterBindings"] = [dict(resourceId="package-input", target=dict(
        workflowId="main", nodeId="package-load", parameterPath=["imagePath"]))]
    candidate["workflows"]["main"]["nodes"].append(dict(nodeId="package-load", kind="operator",
        operatorId="vision.io.image_loader", params=dict(imagePath="not-a-runtime-path.png")))
    for key, purpose in (("page-picture", "page_image"), ("page-icon", "icon")):
        imagePath = local / (key + ".png")
        imagePath.write_bytes(source.read_bytes())
        candidate["resources"]["items"][key] = dict(path=imagePath.name, size=imagePath.stat().st_size,
            sha256=hashlib.sha256(imagePath.read_bytes()).hexdigest(), purpose=purpose)
    candidate["presentation"] = dict(defaultPageId="static", pageOrder=["static"], pages={"static": dict(
        name="Package assets", components=[dict(componentId="image", type="image", props=dict(imageResourceId="page-picture")),
            dict(componentId="button", type="navigation_button", layout=dict(row=1), props=dict(iconResourceId="page-icon"))])})
    uploaded = client.uploadProjectResources(snapshot, candidate, local)
    if service.projectConfiguration.snapshot()["documentRevision"] != snapshot["documentRevision"]:
        raise RuntimeError("upload unexpectedly committed configuration")
    client.writeProjectConfiguration(snapshot, candidate,
        [change["path"] for change in projectChanges(snapshot["payload"], candidate)], "self-test-resource-upload")
    service.projectConfiguration.thread.join(15)
    receipt = service.projectConfiguration.status("self-test-resource-upload")
    if receipt["state"] != "COMMITTED" or service.projectConfiguration.publicationError:
        raise RuntimeError("uploaded resource configuration did not commit: " + str(receipt))
    effective = Path(next(n for n in service.loadedDocument.workflows["main"].nodes if n.nodeId == "package-load").params["imagePath"])
    if effective.read_bytes() != source.read_bytes() or not effective.is_relative_to(root / "configuration-resources"):
        raise RuntimeError("uploaded resource was not copied to immutable Runtime ownership")
    source.unlink()
    for key in ("page-picture", "page-icon"):
        (local / (key + ".png")).unlink()
    _checkStaticAssets(service.loadedDocument)
    return dict(ready=uploaded["uploaded"][0]["state"] == "READY", separateCommit=True,
                immutableCopy=True, documentRevision=receipt["documentRevision"], designerSourceRemoved=True,
                staticAssetsDecoded=True)


def _checkStaticAssets(document):
    from emo_master.core.presentation.assets import pageAssets
    from emo_master.ui.presentation.static_assets import readAsset
    assets = pageAssets(document)
    for key in ("page-picture", "page-icon"):
        image = readAsset(assets[key], 16 * 1024 * 1024, icon=key == "page-icon")
        if image.isNull():
            raise RuntimeError("static page asset failed decode after publication")


def _checkDispatch(service, pb):
    from emo_master.core.project.configuration import projectChanges
    coordinator = service.projectConfiguration
    snapshot = coordinator.snapshot()
    candidate = deepcopy(snapshot["payload"])
    candidate["workflows"]["consumer"] = dict(name="Package consumer", inputs={"image": "image"},
        outputs={"image": "image"}, nodes=[dict(nodeId="input", kind="workflow_input"),
            dict(nodeId="output", kind="workflow_output")],
        edges=[dict(fromNode="input", fromPort="image", toNode="output", toPort="image")])
    candidate["workflowOrder"].append("consumer")
    workflow = candidate["workflows"]["main"]
    workflow["nodes"].extend([
        dict(nodeId="dispatch", kind="dispatch", inputPorts={"image": "image"}, params=dict(targets=[
            dict(targetSlotId=slot, workflowId="consumer", inputs={"image": "image"}, queueCapacity=2)
            for slot in ("left", "right")])),
        dict(nodeId="join", kind="join", params=dict(dispatchNodeId="dispatch", timeoutMs=30000))])
    workflow["edges"].extend([dict(fromNode="capture", fromPort="image", toNode="dispatch", toPort="image"),
                             dict(fromNode="dispatch", fromPort="token", toNode="join", toPort="token")])
    coordinator.start(dict(requestId="self-test-dispatch", expectedRuntimeInstanceId=service.runtimeInstanceId,
        expectedRevision=snapshot["documentRevision"], localPayload=candidate,
        selectedPaths=[change["path"] for change in projectChanges(snapshot["payload"], candidate)]))
    coordinator.thread.join(15)
    if coordinator.thread.is_alive() or coordinator.status("self-test-dispatch")["state"] != "COMMITTED":
        raise RuntimeError("Dispatch self-test configuration did not commit")
    existing = {record.jobId for record in service.jobRepository.all()}
    started = service.StartJob(pb.StartJobRequest(workflow_id="main"), None)
    if not started.ok:
        raise RuntimeError(started.message)
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        records = [record for record in service.jobRepository.all() if record.jobId not in existing]
        service.jobSupervisor.finishRetirements()
        if len(records) == 3 and all(record.isTerminal for record in records) and not service.deviceManager.dispatchGroups:
            break
        time.sleep(.02)
    pids = {record.pid for record in records}
    if len(records) != 3 or any(record.status != "COMPLETED" for record in records) or len(pids) != 3 or os.getpid() in pids:
        raise RuntimeError("Dispatch self-test did not complete three independent workers")
    completions = [json.loads(event.payloadJson) for record in records for event in service._eventsAfter(record.jobId, 0)
                   if event.eventType == "dispatch.invocation.completed"]
    if (len(completions) != 2 or completions[0]["frameIds"] != completions[1]["frameIds"]
            or completions[0]["cycleId"] != completions[1]["cycleId"]
            or completions[0]["invocationId"] == completions[1]["invocationId"]):
        raise RuntimeError("Dispatch self-test frame or invocation identity mismatch")
    if service.deviceManager.frames.frames or service.deviceManager.dispatchGroups or coordinator.cycles:
        raise RuntimeError("Dispatch self-test retained original, plan, or group resources")
    return dict(workerPids=sorted(pids), consumers=2, sameFrame=True, sameCycle=True,
                independentInvocations=True, joinCompleted=True, resourcesRetired=True)
