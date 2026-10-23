"""Validate the delivered PLC projects exclusively against loopback peers."""
from contextlib import ExitStack, contextmanager
from copy import deepcopy
import multiprocessing
from threading import Event
import importlib.util
import json
from pathlib import Path

import pytest

from emo_master import __version__
from emo_master.apps.designer.state.workflow_store import WorkflowStore
from emo_master.apps.runtime.devices.manager import DeviceManager
from emo_master.apps.runtime.devices.ipc import DeviceProxy
from emo_master.apps.runtime.communication.ipc import CommunicationProxy
from emo_master.core.project.runtime_package import trustedDeviceTypes
from emo_master.apps.runtime.workflow.cancellation import CancellationToken
from emo_master.apps.runtime.workflow.context import RunContext
from emo_master.apps.runtime.workflow.runner import WorkflowRunner
from emo_master.core.plugin.registry import PluginRegistry
from emo_master.core.project.models import ProjectDocument
from emo_master.core.project.files import resolveProjectFile
from emo_master.core.workflow.compiler import WorkflowCompiler
from tests.runtime.plc_debug_server import PlcDebugServer


ROOT = Path(__file__).resolve().parents[2]
HERE = ROOT / "field_tests/plc_dual_mc"
spec = importlib.util.spec_from_file_location("dual_plc_fixture", HERE / "build_project.py")
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


@pytest.fixture(scope="module")
def registry():
    scan = PluginRegistry(coreVersion=__version__).scan(ROOT / "src/emo_master/plugins")
    assert not scan.rejectedOperators
    return scan.activeOperators


@contextmanager
def managedDevices(payload, owner="loopback-only", *, prepare=None, cancellation=None):
    """Use the actual authority and data pipes, with caller-owned test peers only."""
    with ExitStack() as stack:
        manager = DeviceManager(trustedDeviceTypes())
        stack.callback(manager.close)
        if prepare is not None:
            prepare(manager)
        cancelled = cancellation or Event()
        manager.load(ProjectDocument.model_validate(payload))
        stack.callback(manager.retire, owner)
        devices = DeviceProxy(manager.openWorker(owner, list(manager.definitions), cancelled,
                                                 multiprocessing.get_context("spawn")), cancelled)
        stack.callback(devices.close)
        communication = CommunicationProxy(manager.openTcpWorker(owner, cancelled,
                                            multiprocessing.get_context("spawn")), cancelled)
        stack.callback(communication.close)
        yield manager, devices, communication


def compiler(registry):
    return WorkflowCompiler(registry, deviceTypes=trustedDeviceTypes())


def run(runner, workflow):
    return runner.run(workflow, {}, RunContext.root("loopback-only", workflow), CancellationToken()).outputs


@pytest.mark.parametrize("simulation", [False, True])
def testGeneratedProjectsMatchAndCompile(simulation, registry):
    path = HERE / "simulation" / fixture.SIMULATION_FILE if simulation else HERE / fixture.FIELD_FILE
    assert resolveProjectFile(path.parent) == path
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload == fixture.build_project(simulation=simulation)
    ProjectDocument.model_validate(payload)
    compiler(registry).compile(payload)
    assert payload["production"] == dict(autoStart=False, mode="single", cycleIntervalMs=100)
    assert payload["runtime"]["startupWorkflowIds"] == ["read_both"]


@pytest.mark.parametrize("simulation", [False, True])
@pytest.mark.parametrize("reverse", [False, True])
def testEveryPublicWorkflowRestoresMemoryAndHonorsBoundaries(simulation, reverse, registry):
    with PlcDebugServer() as first, PlcDebugServer() as second:
        payload = deepcopy(fixture.build_project(simulation=simulation))
        store = WorkflowStore()
        store.loadPayload(payload)
        payload = store.toPayload()
        peers = {"50": first, "51": second}
        original = {796: 60000, 797: 17, 798: 18, 799: 19, 800: 6}
        original_bits = {index: index in {100, 103} for index in range(100, 132)}
        for peer in peers.values():
            peer.words.update(original)
            peer.bits.update(original_bits)
        # Retarget each physical definition once; helper nodes share its binding.
        for station, peer in peers.items():
            payload["devices"]["definitions"]["plc" + station]["connection"].update(
                host="127.0.0.1", port=peer.port)
        for key, workflow in payload["workflows"].items():
            if reverse:
                workflow["nodes"].reverse()
                workflow["edges"].reverse()
        compiled = compiler(registry).compile(payload)
        with managedDevices(payload) as (_, devices, communication):
            runner = WorkflowRunner(compiled, registry, devices=devices, communication=communication)
            result = run(runner, "read_both")
            assert result["plc50_numberValue"] == result["plc51_numberValue"] == 6
            for peer in peers.values():
                assert [(r.command, r.start, r.count) for r in peer.requests] == [(0x0401, 800, 1)]
            for key in payload["workflowOrder"]:
                workflow = payload["workflows"][key]
                if key == "read_both" or workflow["inputs"]:
                    continue
                starts = {station: len(peer.requests) for station, peer in peers.items()}
                result = run(runner, key)
                if "_cycle_" in key:
                    assert result["selectedValueMatches"] is True
                    assert result["firstWordRestored"] is True
                    assert result["original"]["values"] == result["restored"]["values"]
                    requests = peers[key[3:5]].requests[starts[key[3:5]]:]
                    assert [r.command for r in requests] == [0x0401, 0x1401, 0x0401, 0x1401, 0x0401]
                for peer in peers.values():
                    assert peer.words == original
                    assert peer.bits == original_bits
            if not simulation:
                for peer in peers.values():
                    assert all(r.device == "D" and r.count == 1 and
                               r.start in ({796} if r.command == 0x1401 else {796, 800})
                               for r in peer.requests)


def testPhysicalEndpointsAndSingleWordWriteScope():
    payload = fixture.build_project()
    for key, workflow in payload["workflows"].items():
        for node in workflow["nodes"]:
            if node.get("operatorId") not in {fixture.READ, fixture.WRITE}:
                continue
            params = node["params"]
            binding = node["deviceBindings"]["plc"]["deviceId"]
            assert binding == key[:5]
            definition = payload["devices"]["definitions"][binding]
            assert definition["connection"]["host"] == fixture.HOSTS[key[3:5]]
            assert definition["connection"]["port"] == 4998 and params["device"] == "D"
            assert definition["policies"]["connectionRetryCount"] == 0
            assert not {"host", "port", "retryCount", "retryDelayMs"}.intersection(params)
            assert params["dataType"] in {"uint16", "int16"}
            if node["operatorId"] == fixture.WRITE:
                assert params["startAddress"] == 796
                assert len(params["values"]) <= 1
            else:
                assert params["startAddress"] in {796, 800} and params["count"] == 1


@pytest.mark.parametrize("simulation", [False, True])
def testDesignerStorePreservesEveryWire(simulation, registry):
    payload = fixture.build_project(simulation=simulation)
    store = WorkflowStore()
    store.loadPayload(payload)
    for key, expected in payload["workflows"].items():
        assert store.get(key).edges == expected["edges"], key
    saved = store.toPayload()
    compiler(registry).compile(saved)
    reopened = WorkflowStore()
    reopened.loadPayload(saved)
    for key, expected in payload["workflows"].items():
        assert reopened.get(key).edges == expected["edges"], key
