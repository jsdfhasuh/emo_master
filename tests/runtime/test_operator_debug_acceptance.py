from concurrent.futures import ThreadPoolExecutor
import gc
import json
import os
import sys
from pathlib import Path
import threading
from uuid import uuid4

import grpc
import numpy as np
import pytest

from emo_master.apps.designer.operator_editors import EditorKey
from emo_master.apps.designer.services.operator_debug import DebugConnection
from emo_master.apps.designer.services.runtime_client import RuntimeClient
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2_grpc as rpc
from emo_master.apps.runtime.operator_debug.assets import imageBytes
from emo_master.apps.runtime.operator_debug.contracts import DebugError
from emo_master.apps.runtime.operator_debug.manager import OperatorDebugManager
from tests.runtime.operator_debug_fixture import admitted, project
from tests.runtime.test_operator_debug_rpc import runtime as runtime
from tests.runtime.test_operator_debug_sessions import opening, ready, prepare, execute, result, waitFor


def testSpawnedMutationCannotChangeFrozenInputAndFailedSerializationRemovesOutputs(tmp_path):
    spec = dict(entry="tests.runtime.operator_debug_fixture:MutatingImage", version="1",
        inputPorts={"image": "image"}, outputPorts={"image": "image", "before": "integer"},
        paramSchema={"type": "object", "properties": {"large": {"type": "boolean", "default": False}}})
    manager = OperatorDebugManager("runtime", {"test.probe": spec})
    try:
        identity = ready(manager)
        session = manager.sessions[identity["sessionId"]]
        source = session.assets.put(np.full((16, 16), 31, np.uint8), {"kind": "upload"})
        prepared = manager.call("prepare", dict(identity, requestId="input", inputs={"image": {"assetRef": source["assetId"]}}))
        for index, large in enumerate([False, False, True, True]):
            command = manager.call("execute", dict(identity, requestId=f"run-{index}",
                inputSetId=prepared["inputSetId"], paramsJson=json.dumps({"large": large})))
            record = result(manager, identity, command["executionId"])
            assert record["status"] == ("FAILED" if large else "SUCCEEDED")
            if not large:
                assert record["outputs"]["before"] == 31
            assert len(list(session.assets.root.iterdir())) == min(index+2, 3)
    finally:
        manager.close()


def testGrpcMultipartAssetExceedsUnaryLimitAndExpiresAfterReset(runtime):
    with ThreadPoolExecutor(max_workers=4) as pool:
        server = grpc.server(pool, options=[("grpc.max_receive_message_length", 1024*1024)])
        rpc.add_RuntimeServiceServicer_to_server(runtime, server)
        port = server.add_insecure_port("127.0.0.1:0")
        server.start()
        channel = grpc.insecure_channel(f"127.0.0.1:{port}")
        connection = DebugConnection(RuntimeClient(rpc.RuntimeServiceStub(channel)))
        try:
            connection.open(project("vision.preprocess.blur"), EditorKey("draft", "main", "node"), "vision.preprocess.blur")
            waitFor(lambda: connection.snapshot()["session"]["state"] == "READY")
            image = np.random.default_rng(7).integers(0, 256, (1024, 1024, 3), dtype=np.uint8)
            raw = imageBytes(image)
            assert len(raw) > 1024*1024
            asset = connection.upload(raw, "image/png", {"kind": "upload"})
            assert connection.download(asset["assetRef"])["content"] == raw
            started = connection.execute({}, {"image": asset})
            record = waitFor(lambda: (snapshot if (snapshot := connection.snapshot(started["executionId"]))["result"]["status"] != "RUNNING" else None))
            assert record["result"]["status"] == "SUCCEEDED"
            connection.mutation("ResetOperatorDebugSession")
            waitFor(lambda: connection.snapshot()["session"]["generation"] == 2)
            with pytest.raises(Exception) as error:
                connection.download(asset["assetRef"])
            assert error.value.code == "E_DEBUG_RESULT_EXPIRED"
        finally:
            connection.close()
            channel.close()
            server.stop(0).wait(3)


@pytest.mark.skipif(os.environ.get("EMO_DEBUG_STRESS") != "1", reason="explicit 1000 execution / 50 session acceptance run")
def testThousandExecutionsAndFiftyRetirements(record_property):
    import ctypes
    import multiprocessing
    def resources():
        if sys.platform == 'linux':
            # Equivalent native ownership accounting, not a Windows-only skip.
            rssPages = int(Path('/proc/self/statm').read_text().split()[1])
            return dict(handles=len(list(Path('/proc/self/fd').iterdir())),
                handleKind='linux-file-descriptors', threads=threading.active_count(),
                rss=rssPages * os.sysconf('SC_PAGE_SIZE'))
        count = ctypes.c_ulong()
        kernel = ctypes.windll.kernel32
        kernel.GetCurrentProcess.restype = ctypes.c_void_p
        handle = kernel.GetCurrentProcess()
        assert kernel.GetProcessHandleCount(ctypes.c_void_p(handle), ctypes.byref(count))
        class Memory(ctypes.Structure):
            _fields_ = [("cb", ctypes.c_ulong), ("faults", ctypes.c_ulong)] + [
                (name, ctypes.c_size_t) for name in ("peakRss", "rss", "peakPaged", "paged", "peakNonpaged", "nonpaged", "pagefile", "peakPagefile")]
        memory = Memory()
        memory.cb = ctypes.sizeof(memory)
        assert ctypes.windll.psapi.GetProcessMemoryInfo(ctypes.c_void_p(handle), ctypes.byref(memory), memory.cb)
        return dict(handles=count.value, handleKind='windows-process-handles',
                    threads=threading.active_count(), rss=memory.rss)
    before = resources()
    manager = OperatorDebugManager("runtime", admitted())
    roots = []
    try:
        identity = ready(manager)
        session = manager.sessions[identity["sessionId"]]
        roots.append(Path(session.worker.workspace.name))
        inputId = prepare(manager, identity)
        for index in range(1000):
            if index % 100 == 0:
                manager.call("renew", identity)
            try:
                accepted = execute(manager, identity, inputId, requestId=f"execute-{index}")
            except DebugError as error:
                pytest.fail(f"execution {index}: {error}; {manager.call('get', identity)}")
            assert result(manager, identity, accepted["executionId"])["outputs"] == {"value": index+1}
        assert len(session.executions) == 32 and session.assets.used == 0
        assert len(session.events) <= 1000
        manager.call("close", dict(identity, requestId="close"))
        waitFor(lambda: not manager.ownsResources())
        for index in range(49):
            command = opening()
            command["openRequestId"] = uuid4().hex
            state = manager.call("open", command)
            identity = dict(runtimeInstanceId="runtime", sessionId=state["sessionId"], generation=1)
            waitFor(lambda: manager.call("get", identity)["state"] == "READY")
            roots.append(Path(manager.sessions[identity["sessionId"]].worker.workspace.name))
            accepted = execute(manager, identity, prepare(manager, identity))
            assert result(manager, identity, accepted["executionId"])["outputs"] == {"value": 1}
            manager.call("close", dict(identity, requestId="close"))
            waitFor(lambda: not manager.ownsResources())
    finally:
        manager.close()
    gc.collect()
    after = resources()
    record_property("resources", json.dumps(dict(before=before, after=after, sessions=50, executions=1049)))
    assert all(not root.exists() for root in roots)
    assert after["handles"] <= before["handles"] + 12
    assert after["threads"] <= before["threads"] + 1
    assert not multiprocessing.active_children()


def testResetCannotReuseAnOldExecutionOutput():
    manager = OperatorDebugManager("runtime", admitted())
    try:
        identity = ready(manager)
        first = execute(manager, identity, prepare(manager, identity))
        result(manager, identity, first["executionId"])
        manager.call("reset", dict(identity, requestId="reset"))
        waitFor(lambda: manager.call("get", identity)["generation"] == 2)
        identity["generation"] = 2
        with pytest.raises(DebugError) as error:
            manager.call("execution", dict(identity, executionId=first["executionId"]))
        assert error.value.code == "E_DEBUG_RESULT_EXPIRED"
    finally:
        manager.close()


@pytest.mark.parametrize("mode", ["wait", "ignore"])
@pytest.mark.skipif(sys.platform not in {'win32', 'linux'}, reason='requires Windows process handle or Linux pidfd')
def testParentProcessDeathRetiresOrphanWorkerAndOwnedAssets(mode):
    import ctypes
    import multiprocessing
    from tests.runtime.operator_debug_fixture import orphanParent
    context = multiprocessing.get_context("spawn")
    reader, writer = context.Pipe(duplex=False)
    parent = context.Process(target=orphanParent, args=(writer, mode))
    handle = None
    pidfd = None
    if sys.platform == 'win32':
        kernel = ctypes.windll.kernel32
        kernel.OpenProcess.restype = ctypes.c_void_p
    try:
        parent.start()
        writer.close()
        assert reader.poll(15)
        info = json.loads(reader.recv_bytes())
        if sys.platform == 'win32':
            handle = kernel.OpenProcess(0x100000, False, info["pid"])
            assert handle
        else:
            pidfd = os.pidfd_open(info['pid'])
        parent.terminate()
        parent.join(5)
        assert parent.exitcode is not None
        if sys.platform == 'win32':
            assert kernel.WaitForSingleObject(ctypes.c_void_p(handle), 8000) == 0
        else:
            import select
            poller = select.poll()
            poller.register(pidfd, select.POLLIN)
            assert poller.poll(8000), 'orphan worker did not exit'
        assert not Path(info["workspace"]).exists()
    finally:
        if parent.is_alive():
            parent.terminate()
            parent.join(5)
        parent.close()
        reader.close()
        writer.close()
        if handle:
            kernel.CloseHandle(ctypes.c_void_p(handle))
        if pidfd is not None:
            os.close(pidfd)
