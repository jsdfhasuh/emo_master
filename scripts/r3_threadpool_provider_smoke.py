"""One synthetic callback validates Thread Pool ETW fields, not an A18 fix.

Capture is allowed only on an explicitly selected Windows Actions branch.
Reuses the UUID-scoped WPR lifecycle; does not run pytest or modify its gates.
"""
import ctypes as C
from ctypes import wintypes as W
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import threading
import time
import uuid
import xml.etree.ElementTree as ET


PROVIDER = "c861d0e2-a2c1-4d36-9f9c-970bab943a12"
PROFILE = '''<?xml version="1.0" encoding="utf-8"?>
<WindowsPerformanceRecorder Version="1.0"><Profiles>
  <EventCollector Id="TpCollector" Name="EmoThreadPoolSmoke">
    <BufferSize Value="64" /><Buffers Value="64" />
  </EventCollector>
  <EventProvider Id="TpProvider" Name="C861D0E2-A2C1-4D36-9F9C-970BAB943A12"
                 Level="5" />
  <Profile Id="A18Creation.Light.Memory" Name="A18Creation"
           Description="Synthetic callback provider validation only"
           DetailLevel="Light" LoggingMode="Memory">
    <Collectors><EventCollectorId Value="TpCollector"><EventProviders>
      <EventProviderId Value="TpProvider" />
    </EventProviders></EventCollectorId></Collectors>
  </Profile>
</Profiles></WindowsPerformanceRecorder>
'''


def providerSessions():
    """Do not displace an existing classic-provider controller."""
    class Guid(C.Structure):
        _fields_ = [("d1", W.DWORD), ("d2", W.WORD), ("d3", W.WORD), ("d4", C.c_ubyte * 8)]

    guid = Guid.from_buffer_copy(uuid.UUID(PROVIDER).bytes_le)
    api = C.WinDLL("advapi32", use_last_error=True).EnumerateTraceGuidsEx
    api.argtypes = [C.c_int, C.c_void_p, W.ULONG, C.c_void_p, W.ULONG, C.POINTER(W.ULONG)]
    api.restype = W.ULONG
    size = W.ULONG()
    code = api(1, C.byref(guid), C.sizeof(guid), None, 0, C.byref(size))
    if code not in (0, 122) or not 8 <= size.value <= 65536:
        raise RuntimeError(f"provider ownership query unavailable: {code}")
    buffer = C.create_string_buffer(size.value)
    code = api(1, C.byref(guid), C.sizeof(guid), buffer, size, C.byref(size))
    if code:
        raise RuntimeError(f"provider ownership read failed: {code}")
    count, _ = struct.unpack_from("<II", buffer.raw)
    rows, offset = [], 8
    for _ in range(count):
        following, enables, pid, _ = struct.unpack_from("<IIII", buffer.raw, offset)
        for index in range(enables):
            enabled, level, _, logger, properties, _, anyKeyword, allKeyword = struct.unpack_from(
                "<IBBHIIQQ", buffer.raw, offset + 16 + index * 32)
            rows.append(dict(enabled=enabled, level=level, logger=logger, properties=properties,
                             pid=pid, anyKeyword=hex(anyKeyword), allKeyword=hex(allKeyword)))
        if not following:
            break
        offset += following
    return rows


def worker():
    """One real Win32 callback with a recorded function/context/work identity."""
    kernel = C.WinDLL("kernel32", use_last_error=True)
    callbackType = C.WINFUNCTYPE(None, C.c_void_p, C.c_void_p, C.c_void_p)
    kernel.CreateThreadpoolWork.argtypes = [callbackType, C.c_void_p, C.c_void_p]
    kernel.CreateThreadpoolWork.restype = C.c_void_p
    for name in ("SubmitThreadpoolWork", "CloseThreadpoolWork"):
        getattr(kernel, name).argtypes = [C.c_void_p]
        getattr(kernel, name).restype = None
    kernel.WaitForThreadpoolWorkCallbacks.argtypes = [C.c_void_p, W.BOOL]
    kernel.WaitForThreadpoolWorkCallbacks.restype = None
    done = threading.Event()
    record = {"pid": os.getpid(), "main_tid": threading.get_native_id(), "callbacks": []}

    @callbackType
    def callback(instance, context, work):
        record["callbacks"].append(dict(tid=threading.get_native_id(), context=context,
                                        work=work, unix_ns=time.time_ns()))
        done.set()

    record["callback_address"] = C.cast(callback, C.c_void_p).value
    work = kernel.CreateThreadpoolWork(callback, C.c_void_p(0xA18), None)
    if not work:
        raise C.WinError(C.get_last_error())
    record["work_address"] = work
    try:
        record["submit_unix_ns"] = time.time_ns()
        kernel.SubmitThreadpoolWork(work)
        if not done.wait(3):
            raise RuntimeError("synthetic callback did not complete")
        kernel.WaitForThreadpoolWorkCallbacks(work, False)
    finally:
        kernel.CloseThreadpoolWork(work)
    record["end_unix_ns"] = time.time_ns()
    print(json.dumps(record), flush=True)


def validateTrace(path, workerRecord):
    ns = {"e": "http://schemas.microsoft.com/win/2004/08/events/event",
          "t": "http://schemas.microsoft.com/win/2004/08/events/trace"}
    matches, headers, pids = [], [], set()
    for _, event in ET.iterparse(path, events=["end"]):
        if event.tag != "{" + ns["e"] + "}Event":
            continue
        data = {row.get("Name"): row.text for row in event.findall("e:EventData/e:Data", ns)}
        if "EventsLost" in data:
            headers.append({key: int(data[key]) for key in ("EventsLost", "BuffersLost")})
        guid = event.findtext("t:ExtendedTracingInfo/t:EventGuid", "", ns).strip("{}").lower()
        if guid == PROVIDER:
            execution = event.find("e:System/e:Execution", ns).attrib
            pid = int(execution["ProcessID"])
            pids.add(pid)
            def integer(key):
                value = data.get(key, "0") or "0"
                return int(value, 16 if value.strip().lower().startswith("0x") else 10)
            if (pid == workerRecord["pid"] and integer("CallbackFunction") == workerRecord["callback_address"]
                    and integer("CallbackContext") == 0xA18):
                matches.append(dict(opcode=int(event.findtext("e:System/e:Opcode", "-1", ns)),
                                    tid=int(execution["ThreadID"]), pool=integer("PoolId"),
                                    task=integer("TaskId"),
                                    qpc=int(event.find("e:System/e:TimeCreated", ns).get("RawTime")), data=data))
        event.clear()
    # The actual ETL uses an internal TaskId, not the public PTP_WORK pointer.
    # Correlate the opaque TaskId across enqueue/dequeue/start/stop; never bake
    # the observed +0xc8 difference into a Windows-version-dependent decoder.
    callbacks = workerRecord["callbacks"]
    lifecycle = len(callbacks) == 1 and callbacks[0]["work"] == workerRecord["work_address"]
    lifecycle = lifecycle and callbacks[0]["context"] == 0xA18 and len(matches) == 4
    if lifecycle:
        matches.sort(key=lambda row: row["qpc"])
        lifecycle = ([row["opcode"] for row in matches] == [32, 33, 34, 35]
            and matches[0]["tid"] == workerRecord["main_tid"]
            and all(row["tid"] == callbacks[0]["tid"] for row in matches[1:])
            and len({(row["pool"], row["task"]) for row in matches}) == 1
            and bool(matches[0]["pool"] and matches[0]["task"]))
    return {"headers": headers, "providerPids": sorted(pids), "matchingCallbacks": matches,
            "valid": bool(headers) and all(not h["EventsLost"] and not h["BuffersLost"] for h in headers)
                and lifecycle}


def capture(root):
    if (os.name != "nt" or os.environ.get("GITHUB_ACTIONS") != "true"
            or os.environ.get("RUNNER_OS") != "Windows"
            or os.environ.get("GITHUB_REF") != "refs/heads/codex/ci-threadpool-provider-smoke"):
        raise RuntimeError("capture is restricted to the explicit Windows Actions smoke branch")
    from scripts.r3_a18_creation_trace import Trace
    root.mkdir(parents=True, exist_ok=False)
    result = {"role": "synthetic_provider_validation", "source": os.environ.get("GITHUB_SHA"),
              "providerSessionsBefore": providerSessions(), "valid": False,
              "limits": "4 MiB, one classic provider across the disposable runner; decode by PID. "
                        "ProcessExeFilter was ineffective in the actual ETL. Not A18 reproduction or product acceptance."}
    if any(row["enabled"] for row in result["providerSessionsBefore"]):
        raise RuntimeError("an existing session owns the classic provider")
    profile = root / "provider.wprp"
    profile.write_text(PROFILE, encoding="utf-8")
    trace = Trace()
    trace.root, trace.profile = root / "capture", profile
    trace.record.update(role="synthetic_threadpool_provider_smoke", test=None)
    try:
        try:
            trace.start()
            if not trace.started:
                raise RuntimeError("provider capture start failed")
            child = subprocess.run([sys.executable, "-m", __spec__.name, "--worker"],
                                   capture_output=True, timeout=10, check=False)
            (root / "worker.stdout.log").write_bytes(child.stdout)
            (root / "worker.stderr.log").write_bytes(child.stderr)
            result["workerExitCode"] = child.returncode
            if child.returncode:
                raise RuntimeError("synthetic worker failed")
            result["worker"] = json.loads(child.stdout)
        finally:
            trace.finish()
            result["capture"] = trace.record
            result["providerSessionsAfter"] = providerSessions()
        if trace.record["errors"] or not trace.record["retired"]:
            raise RuntimeError("capture did not retire cleanly")
        if any(row["enabled"] for row in result["providerSessionsAfter"]):
            raise RuntimeError("provider still enabled after capture")
        with (root / "tracerpt.log").open("wb") as output:
            decoded = subprocess.run(["tracerpt", str(trace.root / "creation.etl"), "-o", str(root / "events.xml"),
                                      "-of", "XML", "-lr", "-rts"], stdout=output, stderr=output, timeout=30)
        result["decodeExitCode"] = decoded.returncode
        if decoded.returncode:
            raise RuntimeError("provider decode failed")
        result["validation"] = validateTrace(root / "events.xml", result["worker"])
        result["valid"] = result["validation"]["valid"]
        if not result["valid"]:
            raise RuntimeError("matching callback enqueue/start evidence unavailable")
    finally:
        (root / "result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"role": result["role"], "valid": result["valid"],
                      "matched": len(result["validation"]["matchingCallbacks"])}))


if __name__ == "__main__":
    if sys.argv[1:] == ["--worker"]:
        worker()
    elif len(sys.argv) == 3 and sys.argv[1] == "--capture":
        capture(Path(sys.argv[2]).resolve())
    else:
        raise SystemExit("use --worker or --capture <new output directory>")
