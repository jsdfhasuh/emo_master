from __future__ import annotations

import json
import socket
import struct
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from emo_master import __version__
from emo_master.apps.runtime.workflow.cancellation import CancellationToken
from emo_master.apps.runtime.workflow.context import RunContext
from emo_master.apps.runtime.workflow.runner import WorkflowRunner
from emo_master.core.contracts.communication import PlcWriteReceipt
from emo_master.core.plugin.registry import PluginRegistry
from emo_master.core.project.models import ProjectDocument
from emo_master.core.project.devices import DeviceDefinition
from emo_master.core.project.runtime_package import trustedDeviceTypes
from tests.runtime.test_dual_plc_field_project import managedDevices
from emo_master.core.workflow.compiler import WorkflowCompiler


def testRuntimeChainsSlmpReadIntoSlmpWrite(tmp_path: Path) -> None:
    server = _TwoTransactionSlmpServer()
    server.start()
    pluginRoot = Path(__file__).resolve().parents[2] / "src" / "emo_master" / "plugins"
    scan = PluginRegistry(coreVersion=__version__).scan(pluginRoot)
    assert scan.rejectedOperators == {}
    registry = dict(scan.activeOperators)
    events: list[dict[str, object]] = []
    result = _runManaged(_project(server.port), registry, "communication-job", events=events)
    server.join(timeout=3.0)
    assert not server.is_alive()
    assert server.errors == []
    receipt = PlcWriteReceipt.fromPayload(result.outputs["result"])
    assert (receipt.device, receipt.startAddress, receipt.wordCount) == ("D", 798, 2)
    assert result.outputs["firstValue"] == 2
    assert result.outputs["matchesExpected"] is True
    assert len(server.requests) == 2 and server.connections == 1
    assert struct.unpack_from("<H", server.requests[0], 11)[0] == 0x0401
    assert struct.unpack_from("<H", server.requests[1], 11)[0] == 0x1401
    assert struct.unpack("<HH", server.requests[1][21:25]) == (2, 1935)
    operatorLogs = [
        event for event in events if event["eventType"] == "node.log"
    ]
    # Managed I/O is observed at the device authority, not via obsolete
    # private-client start/end log strings. Ordinary logs still exclude values.
    operations = [event for event in events if event["eventType"] == "device.action.completed"]
    assert [(event["deviceId"], event["message"], event["source"]["nodeId"])
            for event in operations] == [("plc", "read", "read"), ("plc", "write", "write")]
    assert all(event["ownerId"] == "communication-job" and event["source"]["slot"] == "plc"
               and event["requestId"] and event["cycleId"] for event in operations)
    assert all(event["result"]["outcome"] == "COMPLETED" for event in operations)
    assert operations[0]["result"]["connectionGeneration"] == operations[1]["result"]["connectionGeneration"]
    assert all(event["payload"]["phase"] == "execute" for event in operatorLogs)
    assert not any("values" in event["payload"]["data"] or "1935" in str(event["payload"])
                   for event in operatorLogs)


def testRuntimeRoutesSlmpBitBooleanThroughIf(tmp_path: Path) -> None:
    server = _TwoTransactionSlmpServer((struct.pack("<H", 1),))
    server.start()
    pluginRoot = Path(__file__).resolve().parents[2] / "src" / "emo_master" / "plugins"
    scan = PluginRegistry(coreVersion=__version__).scan(pluginRoot)
    registry = dict(scan.activeOperators)
    result = _runManaged(_bitProject(server.port), registry, "communication-bit-job", str(tmp_path))
    server.join(timeout=3.0)
    assert not server.is_alive()
    assert server.errors == []
    assert result.outputs["result"] is True


def testRuntimeWritesTcpClientTextOutput(tmp_path: Path) -> None:
    def handler(client: socket.socket) -> None:
        _recvExact(client, 4)
        client.sendall(b"hello\n")

    port, peer, peerErrors = _startTcpPeer(handler)
    registry = _builtinRegistry()
    project = _tcpWriterProject("communication.tcp.client", port, "responseText", "results/client-text")
    events: list[dict[str, object]] = []
    result = _runManaged(project, registry, "tcp-client-job", str(tmp_path), events)
    peer.join(timeout=2.0)
    assert not peer.is_alive()
    assert peerErrors == []
    receipt = result.outputs["receipt"]
    assert isinstance(receipt, dict)
    assert receipt["saved"] is True
    assert json.loads((tmp_path / "results" / "client-text.json").read_text()) == "hello"
    tcpLogs = [
        event
        for event in events
        if event["eventType"] == "node.log"
        and event["payload"]["operatorId"] == "communication.tcp.client"
    ]
    operations = [event for event in events if event["eventType"] == "device.action.completed"]
    assert len(operations) == 1
    operation = operations[0]
    assert (operation["deviceId"], operation["message"], operation["ownerId"], operation["source"]["nodeId"],
            operation["source"]["slot"]) == ("tcp", "exchange", "tcp-client-job", "io", "tcp")
    assert operation["requestId"] and operation["cycleId"]
    assert operation["result"]["sentBytes"] == 4
    assert all("hello" not in str(event["payload"]) for event in tcpLogs)


def testRuntimeWritesTcpReceiveTextOutput(tmp_path: Path) -> None:
    port = _unusedTcpPort()
    registry = _builtinRegistry()
    project = _tcpWriterProject("communication.tcp.receive_once", port, "text", "results/received-text")
    resultBox: dict[str, Any] = {}

    def runWorkflow() -> None:
        resultBox["result"] = _runManaged(project, registry, "tcp-receive-job", str(tmp_path))

    worker = threading.Thread(target=runWorkflow, daemon=True)
    worker.start()
    with _connectEventually(port) as client:
        client.sendall(b"hello\n")
    worker.join(timeout=3.0)
    assert not worker.is_alive()
    result = resultBox["result"]
    receipt = result.outputs["receipt"]
    assert isinstance(receipt, dict)
    assert receipt["saved"] is True
    assert json.loads((tmp_path / "results" / "received-text.json").read_text()) == "hello"


def _runManaged(project, registry, job, workspace="", events=None):
    compiled = WorkflowCompiler(registry, deviceTypes=trustedDeviceTypes()).compile(project)
    with managedDevices(project.model_dump(), job) as (manager, devices, communication):
        result = WorkflowRunner(compiled, registry, devices=devices, communication=communication,
            eventPublisher=(lambda **event: events.append(event)) if events is not None else None).run(
                "main", {}, RunContext.root(job, "main", workspace), CancellationToken())
        if events is not None:
            for deviceId in manager.definitions:
                events.extend(manager.diagnostics.query(manager.scope, deviceId)["records"])
        return result


def _device(deviceTypeId, connection, defaults=None):
    declaration = trustedDeviceTypes()[deviceTypeId]
    return declaration.validateDefinition(DeviceDefinition(name="Loopback only", deviceTypeId=deviceTypeId,
        driverVersion=declaration.version, connection=connection, defaults=defaults or {})).model_dump()


def _builtinRegistry() -> dict[str, Any]:
    pluginRoot = Path(__file__).resolve().parents[2] / "src" / "emo_master" / "plugins"
    scan = PluginRegistry(coreVersion=__version__).scan(pluginRoot)
    assert scan.rejectedOperators == {}
    return dict(scan.activeOperators)


def _tcpWriterProject(
    operatorId: str,
    port: int,
    textPort: str,
    relativePath: str,
) -> ProjectDocument:
    if operatorId == "communication.tcp.client":
        ioParams: dict[str, object] = {"operation": "exchange", "text": "ping"}
        device = _device("device.tcp.client_once", {"host": "127.0.0.1", "port": port},
            dict(responseEncoding="base64", responseTextEncoding="utf-8", connectTimeoutMs=500, responseTimeoutMs=500))
    else:
        ioParams = {}
        device = _device("device.tcp.receive_once", {"bindHost": "127.0.0.1", "port": port},
            dict(acceptTimeoutMs=1000, readTimeoutMs=500, messageEncoding="hex", messageTextEncoding="utf-8"))
    return ProjectDocument.model_validate(
        {
            "schemaVersion": "2.7",
            "project": {
                "projectId": "tcp-text-writer",
                "name": "TCP Text Writer",
                "revision": 1,
                "createdAt": "2026-01-01T00:00:00Z",
                "updatedAt": "2026-01-01T00:00:00Z",
            },
            "entryWorkflowId": "main",
            "workflowOrder": ["main"],
            "workflows": {
                "main": {
                    "name": "Main",
                    "inputs": {},
                    "outputs": {"receipt": "json"},
                    "nodes": [
                        {"nodeId": "input", "kind": "workflow_input"},
                        {
                            "nodeId": "io",
                            "kind": "operator",
                            "operatorId": operatorId,
                            "params": ioParams,
                            "deviceBindings": {"tcp": {"deviceId": "tcp"}},
                        },
                        {
                            "nodeId": "writer",
                            "kind": "operator",
                            "operatorId": "vision.io.result_writer",
                            "params": {
                                "format": "json",
                                "relativePath": relativePath,
                            },
                        },
                        {"nodeId": "output", "kind": "workflow_output"},
                    ],
                    "edges": [
                        {
                            "fromNode": "io",
                            "fromPort": textPort,
                            "toNode": "writer",
                            "toPort": "stringValue",
                        },
                        {
                            "fromNode": "writer",
                            "fromPort": "result",
                            "toNode": "output",
                            "toPort": "receipt",
                        },
                    ],
                    "layout": {"nodePositions": {}},
                }
            },
            "runtime": {}, "presentation": {}, "resources": {}, "production": {}, "globalVariables": {},
            "dependencies": {"operators": []},
            "devices": {"schemaVersion": "1.0", "bindings": {}, "definitions": {"tcp": device}},
        }
    )


def _bitProject(port: int) -> ProjectDocument:
    return ProjectDocument.model_validate(
        {
            "schemaVersion": "2.7",
            "project": {
                "projectId": "slmp-bit-if",
                "name": "SLMP Bit If",
                "revision": 1,
                "createdAt": "2026-01-01T00:00:00Z",
                "updatedAt": "2026-01-01T00:00:00Z",
            },
            "entryWorkflowId": "main",
            "workflowOrder": ["main"],
            "workflows": {
                "main": {
                    "name": "Main",
                    "inputs": {},
                    "outputs": {"result": "boolean"},
                    "nodes": [
                        {"nodeId": "input", "kind": "workflow_input"},
                        {
                            "nodeId": "read",
                            "kind": "operator",
                            "operatorId": "communication.plc.slmp_read",
                            "deviceBindings": {"plc": {"deviceId": "plc"}},
                            "params": {
                                "device": "M",
                                "startAddress": 500,
                                "dataType": "bit",
                                "count": 1,
                                "outputIndex": 0,
                            },
                        },
                        {
                            "nodeId": "if",
                            "kind": "operator",
                            "operatorId": "vision.flow.if",
                            "params": {"mode": "bool"},
                        },
                        {"nodeId": "output", "kind": "workflow_output"},
                    ],
                    "edges": [
                        {
                            "fromNode": "read",
                            "fromPort": "booleanValue",
                            "toNode": "if",
                            "toPort": "value",
                        },
                        {
                            "fromNode": "if",
                            "fromPort": "true",
                            "toNode": "output",
                            "toPort": "result",
                        },
                    ],
                    "layout": {"nodePositions": {}},
                }
            },
            "runtime": {}, "presentation": {}, "resources": {}, "production": {}, "globalVariables": {},
            "dependencies": {"operators": []},
            "devices": {"schemaVersion": "1.0", "bindings": {}, "definitions": {
                "plc": _device("device.plc.mitsubishi_slmp", dict(host="127.0.0.1", port=port,
                    connectTimeoutMs=500, responseTimeoutMs=500))}},
        }
    )


def _project(port: int) -> ProjectDocument:
    common = {
        "device": "D",
        "startAddress": 798,
        "dataType": "uint16",
    }
    return ProjectDocument.model_validate(
        {
            "schemaVersion": "2.7",
            "project": {
                "projectId": "slmp-read-write",
                "name": "SLMP Read Write",
                "revision": 1,
                "createdAt": "2026-01-01T00:00:00Z",
                "updatedAt": "2026-01-01T00:00:00Z",
            },
            "entryWorkflowId": "main",
            "workflowOrder": ["main"],
            "workflows": {
                "main": {
                    "name": "Main",
                    "inputs": {},
                    "outputs": {
                        "result": "plcWriteReceipt",
                        "firstValue": "number",
                        "matchesExpected": "boolean",
                    },
                    "nodes": [
                        {"nodeId": "input", "kind": "workflow_input"},
                        {
                            "nodeId": "read",
                            "kind": "operator",
                            "operatorId": "communication.plc.slmp_read",
                            "deviceBindings": {"plc": {"deviceId": "plc"}},
                            "params": {**common, "count": 2, "outputIndex": 0},
                        },
                        {
                            "nodeId": "write",
                            "kind": "operator",
                            "operatorId": "communication.plc.slmp_write",
                            "deviceBindings": {"plc": {"deviceId": "plc"}},
                            "params": common,
                        },
                        {
                            "nodeId": "compare",
                            "kind": "operator",
                            "operatorId": "vision.compare.number",
                            "params": {"operator": "eq", "rightValue": 2},
                        },
                        {"nodeId": "output", "kind": "workflow_output"},
                    ],
                    "edges": [
                        {
                            "fromNode": "read",
                            "fromPort": "values",
                            "toNode": "write",
                            "toPort": "data",
                        },
                        {
                            "fromNode": "write",
                            "fromPort": "receipt",
                            "toNode": "output",
                            "toPort": "result",
                        },
                        {
                            "fromNode": "read",
                            "fromPort": "numberValue",
                            "toNode": "compare",
                            "toPort": "left",
                        },
                        {
                            "fromNode": "read",
                            "fromPort": "numberValue",
                            "toNode": "output",
                            "toPort": "firstValue",
                        },
                        {
                            "fromNode": "compare",
                            "fromPort": "result",
                            "toNode": "output",
                            "toPort": "matchesExpected",
                        },
                    ],
                    "layout": {"nodePositions": {}},
                }
            },
            "runtime": {}, "presentation": {}, "resources": {}, "production": {}, "globalVariables": {},
            "dependencies": {"operators": []},
            "devices": {"schemaVersion": "1.0", "bindings": {}, "definitions": {
                "plc": _device("device.plc.mitsubishi_slmp", dict(host="127.0.0.1", port=port,
                    connectTimeoutMs=500, responseTimeoutMs=500))}},
        }
    )


class _TwoTransactionSlmpServer(threading.Thread):
    def __init__(
        self,
        responseData: tuple[bytes, ...] = (struct.pack("<HH", 2, 1935), b""),
    ) -> None:
        super().__init__(daemon=True)
        self.responseData = responseData
        self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server.bind(("127.0.0.1", 0))
        self.server.listen(len(responseData))
        self.server.settimeout(2.0)
        self.port = int(self.server.getsockname()[1])
        self.requests: list[bytes] = []
        self.connections = 0
        self.errors: list[BaseException] = []

    def run(self) -> None:
        try:
            with self.server:
                client, _ = self.server.accept()
                self.connections += 1
                with client:
                    client.settimeout(1.0)
                    for responseData in self.responseData:
                        header = _recvExact(client, 9)
                        bodyLength = struct.unpack_from("<H", header, 7)[0]
                        self.requests.append(header + _recvExact(client, bodyLength))
                        body = struct.pack("<H", 0) + responseData
                        response = (
                            b"\xD0\x00\x00\xFF\xFF\x03\x00"
                            + struct.pack("<H", len(body))
                            + body
                        )
                        client.sendall(response)
        except BaseException as exc:  # pragma: no cover - asserted by test
            self.errors.append(exc)


def _recvExact(sock: socket.socket, size: int) -> bytes:
    buffer = bytearray()
    while len(buffer) < size:
        chunk = sock.recv(size - len(buffer))
        if not chunk:
            raise ConnectionError("peer closed early")
        buffer.extend(chunk)
    return bytes(buffer)


def _startTcpPeer(
    handler: Callable[[socket.socket], None],
) -> tuple[int, threading.Thread, list[BaseException]]:
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    server.settimeout(2.0)
    port = int(server.getsockname()[1])
    errors: list[BaseException] = []

    def run() -> None:
        try:
            with server:
                client, _ = server.accept()
                with client:
                    client.settimeout(1.0)
                    handler(client)
        except BaseException as exc:  # pragma: no cover - asserted by caller
            errors.append(exc)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return port, thread, errors


def _unusedTcpPort() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _connectEventually(port: int) -> socket.socket:
    deadline = time.monotonic() + 1.0
    while True:
        try:
            return socket.create_connection(("127.0.0.1", port), timeout=0.2)
        except OSError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.01)
