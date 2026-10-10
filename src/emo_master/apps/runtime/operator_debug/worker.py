"""Spawn worker and bounded JSON-only pipes; no production database or Job."""
from __future__ import annotations

import importlib
import multiprocessing
import os
from pathlib import Path
import queue
import shutil
import tempfile
import threading
import time

from emo_master.apps.runtime.jobs.heartbeat import HeartbeatCell, monotonicMs
from emo_master.apps.runtime.operator_debug.contracts import encode, parse, MAX_INLINE_BYTES, MAX_REQUEST_BYTES


def _retireOrphanWorkspace(workspace):
    path = Path(workspace).resolve()
    root = Path(tempfile.gettempdir()).resolve()
    if path.parent != root or not path.name.startswith("emo-operator-debug-"):
        return
    os.chdir(root)
    try:
        shutil.rmtree(path)
    except OSError:
        # Resource handles still owned by uncooperative code die with this process.
        pass


def watchParent(workspace, cancellation, stop, heartbeat, finished):
    orphanedAt = None
    while not finished.is_set():
        heartbeat.publish()
        parent = multiprocessing.parent_process()
        if parent is not None and not parent.is_alive():
            cancellation.set()
            stop.set()
            orphanedAt = orphanedAt or time.monotonic()
            if time.monotonic() - orphanedAt >= 3:
                _retireOrphanWorkspace(workspace)
                os._exit(70)
        finished.wait(.2)


def runOperatorDebug(specJson, commands, events, cancellation, stop, heartbeat):
    from emo_master.apps.runtime.execution.operator_executor import OperatorExecutor
    from emo_master.apps.runtime.workflow.cancellation import CancellationToken
    from emo_master.apps.runtime.workflow.context import RunContext
    from emo_master.core.workflow.models import CompiledNode
    from emo_master.apps.runtime.operator_debug.assets import DebugAssets, freezeOutputs
    from emo_master.apps.runtime.operator_debug.data import materialize
    from emo_master.apps.runtime.operator_debug.state import DebugVariables, DebugCounters

    spec = parse(specJson)
    os.chdir(spec["workspacePath"])
    finished = threading.Event()

    def beat():
        orphanedAt = None
        while not finished.is_set():
            heartbeat.publish()
            parent = multiprocessing.parent_process()
            if parent is not None and not parent.is_alive():
                cancellation.set()
                stop.set()
                orphanedAt = orphanedAt or time.monotonic()
                if time.monotonic() - orphanedAt >= 3:
                    _retireOrphanWorkspace(spec["workspacePath"])
                    os._exit(70)
            finished.wait(.2)

    thread = threading.Thread(target=beat, name="operator-debug-heartbeat", daemon=True)
    thread.start()
    executor = None
    variables = DebugVariables(spec.get("variableDefinitions", {}))
    counters = DebugCounters(variables)
    context = RunContext("", spec["workflowId"], "", projectId=spec["projectId"], callerNodeId=spec["nodeId"],
                         workspacePath=spec["workspacePath"])
    primary = None

    def send(value):
        events.send_bytes(encode(value).encode())

    def publish(**event):
        send({"kind": "log", "event": {key: value for key, value in event.items() if key != "context"},
              "executionId": event["context"].nodeRunId})

    try:
        module, className = spec["entry"].split(":")
        operator = getattr(importlib.import_module(module), className)
        registry = {spec["operatorId"]: operator}
        executor = OperatorExecutor(registry, eventPublisher=publish, globalVariables=variables, globalCounters=counters)
        lastParams = None
        send({"kind": "ready"})
        while not stop.is_set():
            if not commands.poll(.05):
                continue
            command = parse(commands.recv_bytes(MAX_INLINE_BYTES).decode())
            context = RunContext("", spec["workflowId"], "", projectId=spec["projectId"],
                                 callerNodeId=spec["nodeId"], nodeRunId=command["executionId"],
                                 workspacePath=spec["workspacePath"])
            started = time.monotonic()
            outputAssets = DebugAssets(spec["workspacePath"], budget=command["outputBudget"])
            try:
                if lastParams is not None and lastParams != command["params"]:
                    executor.closeSession(context)
                    executor = OperatorExecutor(registry, eventPublisher=publish, globalVariables=variables, globalCounters=counters)
                lastParams = command["params"]
                variables.values = DebugVariables(spec.get("variableDefinitions", {}), command.get("variables", {})).values
                node = CompiledNode(spec["nodeId"], "operator", spec["operatorId"], command["inputPorts"],
                                    command["outputPorts"], command["params"], paramSchema=spec["paramSchema"])
                outputs, metrics, diagnostics = executor.execute(node, materialize(command["inputs"], DebugAssets(spec["workspacePath"])), context,
                                                                  CancellationToken(cancellation))
                inline, references = freezeOutputs(outputs, outputAssets,
                    dict(kind="debug-output", projectId=spec["projectId"], workflowId=spec["workflowId"], nodeId=spec["nodeId"],
                         executionId=command["executionId"], captureId=command["executionId"]))
                result = dict(status="SUCCEEDED", outputs=inline, outputAssets=references, metrics=metrics, diagnostics=diagnostics)
            except Exception as error:
                outputAssets.discard()
                code = getattr(error, "code", "E_EXEC_FAILED")
                result = dict(status="CANCELLED" if code == "E_CANCELLED" else "FAILED", code=code,
                              message=str(error)[:2048], diagnostics=getattr(error, "diagnostics", {}))
                if code == "E_RESOURCE_CLEANUP_FAILED":
                    primary = error
                    stop.set()
            result.update(kind="result", executionId=command["executionId"], elapsedMs=(time.monotonic()-started)*1000,
                          finishedAtMs=monotonicMs(), variables=variables.values)
            try:
                send(result)
            except ValueError:
                outputAssets.discard()
                send(dict(kind="result", executionId=command["executionId"], status="FAILED",
                          code="E_DEBUG_LIMIT", message="result exceeds inline transport budget", variables=variables.values))
    except BaseException as error:
        primary = error
        try:
            send(dict(kind="fault", code=getattr(error, "code", "E_DEBUG_WORKER_FAILED"), message=str(error)[:2048]))
        except (OSError, ValueError):
            pass
    finally:
        try:
            if executor is not None:
                executor.closeSession(context, primary)
            if primary is not None and getattr(primary, "diagnostics", {}).get("resourceCleanup"):
                send(dict(kind="fault", code="E_RESOURCE_CLEANUP_FAILED", message="operator cleanup failed"))
            send({"kind": "closed"})
        except BaseException as error:
            try:
                send(dict(kind="fault", code="E_RESOURCE_CLEANUP_FAILED", message=str(error)[:2048]))
            except (OSError, ValueError):
                pass
        finally:
            finished.set()
            thread.join(1)
            commands.close()
            events.close()
            parent = multiprocessing.parent_process()
            if parent is not None and not parent.is_alive():
                _retireOrphanWorkspace(spec["workspacePath"])


class DebugWorker:
    """Own all process/pipe/thread handles until retirement is confirmed."""

    def __init__(self, spec):
        context = multiprocessing.get_context("spawn")
        self.cancel = context.Event()
        self.stop = context.Event()
        self.heartbeat = HeartbeatCell(context)
        childRead, self.commandPipe = context.Pipe(duplex=False)
        self.eventPipe, childWrite = context.Pipe(duplex=False)
        self.outbound: queue.Queue = queue.Queue(maxsize=1)
        self.inbound: queue.Queue = queue.Queue(maxsize=64)
        self.finished = threading.Event()
        self.fault = ""
        self.droppedLogs = 0
        self.retired = False
        self.processClosed = False
        self.started = monotonicMs()
        self.workspace = tempfile.TemporaryDirectory(prefix="emo-operator-debug-")
        target, extra = runOperatorDebug, ()
        self.snapshotAck = context.Event()
        if spec.get("kind") == "workflow":
            from emo_master.apps.runtime.workflow_debug.worker import runWorkflowDebug
            target, extra = runWorkflowDebug, (self.snapshotAck,)
        self.process = context.Process(target=target,
            args=(encode(dict(spec, workspacePath=self.workspace.name), MAX_REQUEST_BYTES), childRead, childWrite,
                  self.cancel, self.stop, self.heartbeat, *extra),
            name="emo-operator-debug")
        self.reader = threading.Thread(target=self._read, name="debug-pipe-reader", daemon=True)
        self.writer = threading.Thread(target=self._write, name="debug-pipe-writer", daemon=True)
        self.processStarted = False
        self.childRead, self.childWrite = childRead, childWrite

    def start(self):
        try:
            self.process.start()
            self.processStarted = True
        finally:
            self.childRead.close()
            self.childWrite.close()
        self.reader.start()
        self.writer.start()

    def _read(self):
        try:
            while not self.finished.is_set():
                if not self.eventPipe.poll(.05):
                    continue
                event = parse(self.eventPipe.recv_bytes(MAX_INLINE_BYTES).decode())
                if event.get("kind") == "log":
                    try:
                        self.inbound.put_nowait(event)
                    except queue.Full:
                        self.droppedLogs += 1
                else:
                    while not self.finished.is_set():
                        try:
                            self.inbound.put(event, timeout=.05)
                            break
                        except queue.Full:
                            continue
        except EOFError:
            pass
        except (OSError, ValueError) as error:
            self.fault = str(error)[:512]
        finally:
            self.eventPipe.close()

    def _write(self):
        try:
            while not self.finished.is_set():
                try:
                    value = self.outbound.get(timeout=.05)
                except queue.Empty:
                    continue
                self.commandPipe.send_bytes(value)
        except (OSError, ValueError) as error:
            self.fault = str(error)[:512]
        finally:
            self.commandPipe.close()

    def execute(self, command):
        self.cancel.clear()
        self.outbound.put_nowait(encode(command).encode())

    def control(self, command):
        self.outbound.put_nowait(encode(command).encode())

    def requestStop(self):
        self.cancel.set()
        self.stop.set()

    def terminate(self):
        if not self.retired and not self.processClosed and self.process.is_alive():
            self.process.terminate()
            return True
        return False

    def retire(self):
        if self.retired:
            return True
        if self.processClosed:
            self.workspace.cleanup()
            self.retired = True
            return True
        if not self.processStarted and self.process.pid is None:
            self.commandPipe.close()
            self.eventPipe.close()
            self.process.close()
            self.processClosed = True
            self.workspace.cleanup()
            self.retired = True
            return True
        if self.process.is_alive():
            return False
        self.process.join(timeout=0)
        # Drain final messages before closing the reader, including cleanup faults.
        if self.reader.is_alive() or not self.inbound.empty():
            return False
        self.finished.set()
        if self.writer.ident is not None:
            self.writer.join(timeout=0)
        if self.writer.is_alive():
            return False
        self.process.close()
        self.processClosed = True
        self.commandPipe.close()
        self.eventPipe.close()
        self.workspace.cleanup()
        self.retired = True
        return True
