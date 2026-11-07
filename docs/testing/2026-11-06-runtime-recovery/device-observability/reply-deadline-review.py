"""Compare the reply-send deadline without editing product code or using sleep.

Uses each source's unchanged DeviceBridge.run, native Pipe framing and the real
pipe_io.sendBytes. Only module-local clocks and the serialization pause are
controlled. The successful-send wrapper stops the one-request bridge afterwards.
"""
import argparse
from hashlib import sha256
import json
import multiprocessing
from pathlib import Path
import subprocess
import sys
from threading import Event
from types import ModuleType, SimpleNamespace


ROOT = Path(__file__).resolve().parents[4]
BASE = "cdf1250ccc41b78f79b25b87e6db197809875384"
IPC = "src/emo_master/apps/runtime/devices/ipc.py"
PIPE_IO = "src/emo_master/apps/runtime/communication/pipe_io.py"
sys.path.insert(0, str(ROOT / "src"))


def load(name, source, package):
    module = ModuleType(name)
    module.__package__ = package
    exec(compile(source, name, "exec"), module.__dict__)
    return module


def observe(source, serialization_pause, transport_pause):
    clock = SimpleNamespace(now=100.0)
    clock.monotonic = lambda: clock.now
    bridge_module = load("reply_deadline_bridge", source, "emo_master.apps.runtime.devices")
    pipe_module = load("reply_deadline_pipe", (ROOT / PIPE_IO).read_text(),
                       "emo_master.apps.runtime.communication")
    bridge_module.time = pipe_module.time = clock
    bridge_module.receiveBytes = pipe_module.receiveBytes
    assert bridge_module.IPC_TIMEOUT == 2.0
    parent, child = multiprocessing.get_context("spawn").Pipe()
    calls, transport_errors = [], []
    observed = dict(serializationPauseSeconds=serialization_pause,
                    transportPauseSeconds=transport_pause, ipcTimeoutSeconds=bridge_module.IPC_TIMEOUT)

    def execute(*args, **kwargs):
        calls.append(kwargs["requestId"])
        return {"completed": True}

    bridge = bridge_module.DeviceBridge(SimpleNamespace(execute=execute), "review-job", parent, Event())
    bridge.onTransportError = transport_errors.append

    def dumps(value):
        observed["serializationStarted"] = clock.now
        result = json.dumps(value)
        clock.now += serialization_pause
        observed["serializationEnded"] = clock.now
        return result

    def send(connection, data, cancelled, deadline):
        observed.update(sendEntered=clock.now, sendDeadline=deadline,
                        sendBudgetAtEntry=deadline - clock.now)
        clock.now += transport_pause
        try:
            pipe_module.sendBytes(connection, data, cancelled, deadline)
        except Exception as error:
            observed["sendErrorType"] = type(error).__name__
            raise
        observed["sendErrorType"] = None
        bridge.stopped.set()  # Retire after this completed reply, never before I/O.

    bridge_module.json = SimpleNamespace(loads=json.loads, dumps=dumps)
    bridge_module.sendBytes = send
    request = dict(sequence=1, kind="device_action", deviceId="simulated", action="read",
                   requestId="review-request", cycleId="review-cycle", params={})
    try:
        child.send_bytes(json.dumps(request).encode())
        bridge.run()
        assert parent.closed and child.poll(.1)
        try:
            observed["reply"] = json.loads(child.recv_bytes())
            observed["peerOutcome"] = "REPLY"
        except EOFError:
            observed["reply"] = None
            observed["peerOutcome"] = "EOF"
        observed.update(executeCalls=len(calls), parentClosed=parent.closed,
                        failureDiagnostics=transport_errors)
        assert calls == ["review-request"]
        return observed
    finally:
        parent.close()
        child.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expect", choices=("regression", "restored"), default="regression")
    expected = parser.parse_args().expect
    baseline = subprocess.run(["git", "show", f"{BASE}:{IPC}"], cwd=ROOT,
                              check=True, capture_output=True, text=True).stdout
    current = (ROOT / IPC).read_text()
    scenarios = [("control", 0.0, 0.0), ("serialization_pause", 2.25, 0.0),
                 ("transport_pause", 0.0, 2.25)]
    rows = {name: {"baseline": observe(baseline, serialization, transport),
                   "current": observe(current, serialization, transport)}
            for name, serialization, transport in scenarios}
    report = dict(baseCommit=BASE, sourcePath=IPC, expected=expected,
                  sourceSha256={"baseline": sha256(baseline.encode()).hexdigest(),
                                "current": sha256(current.encode()).hexdigest(),
                                "pipeIo": sha256((ROOT / PIPE_IO).read_bytes()).hexdigest()},
                  method="Unchanged run methods, native Pipe, real sendBytes; local fake clocks; no sleep/spawn.",
                  scenarios=rows)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    for implementation in ("baseline", "current"):
        assert rows["control"][implementation]["peerOutcome"] == "REPLY"
        assert rows["transport_pause"][implementation]["peerOutcome"] == "EOF"
        assert rows["transport_pause"][implementation]["sendErrorType"] == "TimeoutError"
    paused = rows["serialization_pause"]
    assert paused["baseline"]["peerOutcome"] == "REPLY"
    assert paused["baseline"]["sendBudgetAtEntry"] == 2.0
    assert paused["current"]["peerOutcome"] == ("EOF" if expected == "regression" else "REPLY")
    assert paused["current"]["sendBudgetAtEntry"] == (-.25 if expected == "regression" else 2.0)


if __name__ == "__main__":
    main()
