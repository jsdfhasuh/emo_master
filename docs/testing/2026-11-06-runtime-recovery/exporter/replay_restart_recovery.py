"""Native replay of the retained retirement counterexample against repaired code.

The original worker and callback hand-off are preserved. Production 500 ms /
10 s deadlines are unchanged. This is a controlled real-process regression,
not a natural-load, Web, desktop, or hardware acceptance run.
"""
import argparse
from functools import partial
import hashlib
import json
import multiprocessing
from multiprocessing.shared_memory import SharedMemory
import os
from pathlib import Path
import queue
import tempfile
import threading
import time

import cv2
import numpy as np
from emo_master.apps.runtime.presentation.collector import ResultCollector
from emo_master.apps.runtime.presentation.exporter import ExportPool, encodeWorker
from emo_master.apps.runtime.presentation import exporter as exporter_module

ORIGINAL_REPRO_SHA256 = '33d99fb8841f00a0fa7a3ef41acdc0f9faf560aff77208ead4c2578264b07f0a'


def worker(connection, memory_name, startup_failure, encode_claimed, worker_reports):
    worker_reports.put({"kind": "worker_entered", "pid": os.getpid(),
                        "atNs": time.monotonic_ns(), "startup_failure": bool(startup_failure.value)})
    if startup_failure.value:
        # Parent's unchanged _spawn.poll(10) must time out and reap this process.
        threading.Event().wait(30)
        return
    import cv2
    original = cv2.imencode

    def one_stalled_encode(*args, **kwargs):
        with encode_claimed.get_lock():
            should_stall = not encode_claimed.value
            if should_stall:
                encode_claimed.value = True
        if should_stall:
            worker_reports.put({"kind": "private_copy_encoding_stalled", "pid": os.getpid(),
                                "atNs": time.monotonic_ns()})
            # encodeWorker already emitted a real verified snapshot ack.
            threading.Event().wait(30)
        return original(*args, **kwargs)

    cv2.imencode = one_stalled_encode
    try:
        encodeWorker(connection, memory_name)
    finally:
        cv2.imencode = original


def item(ordinal):
    return {"rawBytes": 0, "identity": {"jobId": "diagnostic-job", "resultKey": f"result-{ordinal}",
            "resultOrdinal": ordinal, "invocationId": f"invocation-{ordinal}", "resultScopeId": "scope"}}


def inspect_slot(slot):
    with slot["lock"]:
        process = slot["process"]
        credit = slot["free"].acquire(False)
        if credit:
            assert not slot["free"].acquire(False), "a raw lane may expose only one reservation"
            slot["free"].release()
        return {"atNs": time.monotonic_ns(), "quarantined": bool(slot["quarantined"].value),
                "active": slot["active"], "busy": slot["busy"], "pending": sorted(slot["pending"]),
                "pendingDescriptors": {str(k): dict(v) for k, v in slot["pendingDescriptors"].items()},
                "queueSize": slot["queue"].qsize(), "currentDiagnostic": dict(slot["currentDiagnostic"]) if slot["currentDiagnostic"] else None,
                "process": None if process is None else {"pid": process.pid, "alive": process.is_alive(), "exitcode": process.exitcode},
                "creditAvailable": credit, "ownerThreadAlive": slot["thread"].is_alive()}


def wait_for_retirements(pool, slot, count, allowance):
    deadline = time.monotonic() + allowance
    while time.monotonic() < deadline:
        with slot["lock"]:
            done = (len(pool.diagnostics) == count and slot["currentDiagnostic"] is None
                    and slot["queue"].empty())
        if done:
            return
        time.sleep(.02)
    raise AssertionError("owner did not settle within existing startup/reap allowance")


def run_case(fail_restart, checkpoint):
    context = multiprocessing.get_context("spawn")
    startup_failure = context.Value("b", False)
    encode_claimed = context.Value("b", False)
    worker_reports = context.Queue()
    first_callback = threading.Event()
    return_from_callback = threading.Event()
    callbacks, states, captures, retirement_events = [], [], [], []
    expected_pixels = {33: 29, 34: 173, 40: 241, 41: 137}
    result = {"case": "replacement_ready_timeout" if fail_restart else "healthy_replacement_control",
              "captures": captures, "states": states, "callbacks": callbacks,
              "retirement_events": retirement_events}
    with tempfile.TemporaryDirectory(prefix="f308-image-repair-replay-") as temporary:
        pool = None

        def callback(task, path, outcome):
            record = {"resultOrdinal": task["resultOrdinal"], "outcome": outcome,
                      "pathExists": path is not None and path.exists(),
                      "callbackNs": time.monotonic_ns(), "diagnostic": dict(task["exportDiagnostic"])}
            if path is not None:
                pixels = cv2.imdecode(np.frombuffer(path.read_bytes(), np.uint8), cv2.IMREAD_UNCHANGED)
                record.update(decodedShape=list(pixels.shape), expectedPixelValue=expected_pixels[task["resultOrdinal"]],
                              decodedAllExpected=bool(np.all(pixels == expected_pixels[task["resultOrdinal"]])),
                              decodedSha256=hashlib.sha256(pixels.tobytes()).hexdigest())
            callbacks.append(record)
            if task["resultOrdinal"] == 33:
                assert pool.slots[0]["process"] is None, "old encoder must already be actually reaped"
                first_callback.set()
                if not return_from_callback.wait(5):
                    raise RuntimeError("controller did not admit successor")

        pool = ExportPool(Path(temporary), callback, worker=partial(worker, startup_failure=startup_failure,
                            encode_claimed=encode_claimed, worker_reports=worker_reports))
        pool.retirementCallback = lambda diagnostic: retirement_events.append(dict(diagnostic))
        slot = pool.slots[0]
        memory_names = [owned["memory"].name for owned in pool.slots]
        collector = ResultCollector({"plan": "{}", "slots": [pool.descriptors()[0]]},
                                    lambda message: pool.submit(message["descriptor"]))

        def capture(ordinal, value):
            identity = item(ordinal)
            value = collector.image("image-source", np.full((32, 48, 3), value, np.uint8), identity)
            captures.append({"ordinal": ordinal, "result": value,
                             "sourceObservations": identity.get("sourceObservations")})
            return value

        try:
            assert capture(33, expected_pixels[33])["pendingImage"]
            assert first_callback.wait(5), "first encode timeout did not complete"
            assert callbacks[0]["outcome"] == "EXPORT_TIMEOUT"
            assert callbacks[0]["diagnostic"]["stage"] == "completion_wait"
            assert callbacks[0]["diagnostic"]["laneReleaseProof"] == "verified_private_snapshot"
            states.append({"label": "old_encoder_dead_before_successor", **inspect_slot(slot)})
            assert states[-1]["process"] is None and states[-1]["creditAvailable"]
            assert capture(34, expected_pixels[34])["pendingImage"]
            states.append({"label": "successor_published_during_callback", **inspect_slot(slot)})
            assert states[-1]["pending"] == [0] and not states[-1]["creditAvailable"]
            startup_failure.value = fail_restart
            return_from_callback.set()
            busy = capture(35, 99)
            assert busy["reasonCode"] == "BUDGET_EXCEEDED"
            wait_for_retirements(pool, slot, 2, 14)
            settled = inspect_slot(slot)
            states.append({"label": "callbacks_and_queue_finished", **settled})
            assert not settled["quarantined"] and not settled["active"] and not settled["busy"]
            assert settled["pending"] == [] and settled["pendingDescriptors"] == {}
            assert settled["queueSize"] == 0 and settled["currentDiagnostic"] is None
            assert settled["creditAvailable"] and settled["ownerThreadAlive"]
            first, successor = list(pool.diagnostics)
            assert first["laneReleased"] and successor["laneReleased"]
            assert first["laneReleaseProof"] == "verified_private_snapshot"
            assert first["reapedNs"] <= successor["captureAcquiredNs"]
            assert first["taskId"] != successor["taskId"]
            if fail_restart:
                assert settled["process"] is None
                assert first["startupErrorType"] == "RuntimeError"
                assert first["retirementErrorType"] == "RuntimeError"
                assert first["startupReapedNs"] <= first["retirementEndedNs"] <= successor["ownerStartedNs"]
                assert successor["outcome"] == "EXPORT_TIMEOUT"
                assert successor["laneReleaseProof"] == "not_dispatched" and "dispatchNs" not in successor
                assert len(retirement_events) == 1 and retirement_events[0]["ownerPhase"] == "retired"
            else:
                assert settled["process"]["alive"]
                assert not retirement_events

            # Remove only the controlled failure. A normal fresh descriptor
            # drives the repaired pool's existing on-demand startup branch.
            startup_failure.value = False
            assert capture(40, expected_pixels[40])["pendingImage"]
            wait_for_retirements(pool, slot, 3, 12)
            recovered = inspect_slot(slot)
            states.append({"label": "first_fresh_request_retired", **recovered})
            assert not recovered["quarantined"] and not recovered["busy"] and not recovered["active"]
            assert not recovered["pending"] and recovered["creditAvailable"]
            assert recovered["process"]["alive"]
            fresh = list(pool.diagnostics)[2]
            assert fresh["laneReleased"]
            if fail_restart:
                assert fresh["startupPhase"] == "export" and "startupReadyNs" in fresh
                assert "startupErrorType" not in fresh
                # A real spawn may consume the unchanged 500 ms task budget.
                # That request must retire cleanly; do not inflate its deadline.
                assert fresh["outcome"] in {"AVAILABLE", "EXPORT_TIMEOUT"}
                if fresh["outcome"] == "EXPORT_TIMEOUT":
                    assert fresh["laneReleaseProof"] == "not_dispatched" and "dispatchNs" not in fresh
            else:
                assert fresh["outcome"] == "AVAILABLE"

            # One further normal capture verifies fresh pixels after readiness,
            # including when startup consumed the preceding frame's own budget.
            assert capture(41, expected_pixels[41])["pendingImage"]
            wait_for_retirements(pool, slot, 4, 3)
            final = inspect_slot(slot)
            states.append({"label": "next_normal_image_exported", **final})
            assert not final["quarantined"] and not final["active"] and not final["busy"]
            assert not final["pending"] and not final["pendingDescriptors"] and final["creditAvailable"]
            assert final["process"]["pid"] == recovered["process"]["pid"]
            assert callbacks[-1]["resultOrdinal"] == 41 and callbacks[-1]["outcome"] == "AVAILABLE"
            assert [record["resultOrdinal"] for record in callbacks] == [33, 34, 40, 41]
            assert all(record["decodedAllExpected"] for record in callbacks if record["pathExists"])
            assert all(record["laneReleased"] for record in pool.diagnostics)
            assert not any(error.startswith("callback:") for error in pool.errors)
            result["assertionsPassed"] = True
        except BaseException as error:
            result["failure"] = {"type": type(error).__name__, "detail": str(error)}
            raise
        finally:
            result.update(diagnostics=list(pool.diagnostics), errors=list(pool.errors), stats=dict(pool.stats))
            return_from_callback.set()
            pool.close()
            unlinked = []
            for name in memory_names:
                try:
                    leaked = SharedMemory(name=name)
                except FileNotFoundError:
                    unlinked.append(True)
                else:
                    leaked.close()
                    unlinked.append(False)
            result["after_close"] = {
                "allProcessesReaped": all(s["process"] is None for s in pool.slots),
                "allOwnersExited": all(not s["thread"].is_alive() for s in pool.slots),
                "allQuarantinesCleared": all(not s["quarantined"].value for s in pool.slots),
                "allSharedMemoryUnlinked": all(unlinked)}
            reports = []
            while True:
                try:
                    reports.append(worker_reports.get_nowait())
                except queue.Empty:
                    break
            worker_reports.close()
            worker_reports.join_thread()
            result["worker_reports"] = reports
            checkpoint.write_text(json.dumps(result, indent=2) + "\n")
    assert all(result["after_close"].values())
    entered = [record for record in result["worker_reports"] if record["kind"] == "worker_entered"]
    assert len(entered) == (4 if fail_restart else 3), "only one failed replacement and one normal retry"
    assert sum(bool(record["startup_failure"]) for record in entered) == int(fail_restart)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    results = []
    for failure in (False, True):
        checkpoint = args.output.with_name(args.output.stem + ("-failure" if failure else "-healthy") + ".json")
        result = run_case(failure, checkpoint)
        results.append(result)
        print(json.dumps({"case": result["case"], "errors": result["errors"],
                          "settled": result["states"][2],
                          "freshOutcomes": [{"ordinal": record["resultOrdinal"], "outcome": record["outcome"],
                                             "pixelsMatch": record.get("decodedAllExpected")}
                                            for record in result["callbacks"] if record["resultOrdinal"] >= 40],
                          "after_close": result["after_close"]}), flush=True)
        args.output.write_text(json.dumps({
            "baseCommit": "cdf1250ccc41b78f79b25b87e6db197809875384", "workingTree": "repo-repair",
            "originalReproSha256": ORIGINAL_REPRO_SHA256,
            "exporterSha256": hashlib.sha256(Path(exporter_module.__file__).read_bytes()).hexdigest(),
            "cases": results}, indent=2) + "\n")
