"""Timing and non-Qt preparation for the Designer entry point."""
from __future__ import annotations

import math
import os
from concurrent.futures import CancelledError
from queue import SimpleQueue
from threading import Event
from time import perf_counter

from emo_master.apps.designer.startup_settings import DesignerStartupOptions


class StartupTiming:
    def __init__(self, entryTime: float) -> None:
        raw = os.environ.pop("EMO_DESIGNER_LAUNCH_STARTED", "")
        try:
            started = float(raw)
        except ValueError:
            started = entryTime
        self.started = started if math.isfinite(started) and 0 <= started <= entryTime else entryTime
        self.updates: SimpleQueue[str] = SimpleQueue()
        self.stage = ""
        self.stageStarted = entryTime

    def mark(self, name: str) -> None:
        print(f"[designer:start] {name} total={perf_counter() - self.started:.3f}s", flush=True)

    def begin(self, stage: str) -> None:
        self.finish()
        self.stage = stage
        self.stageStarted = perf_counter()
        self.updates.put(stage)
        self.mark(stage + " start")

    def finish(self, outcome: str = "done") -> None:
        if not self.stage:
            return
        now = perf_counter()
        print(f"[designer:start] {self.stage} {outcome} "
              f"elapsed={now - self.stageStarted:.3f}s total={now - self.started:.3f}s", flush=True)
        self.stage = ""


def prepareRuntime(timing: StartupTiming, cancelled: Event, *, options: DesignerStartupOptions | None = None):
    if cancelled.is_set():
        return None
    timing.begin("runtime-imports")
    from emo_master.apps.designer.services.runtime_client import RuntimeClient

    if options is None:
        target = os.getenv("EMO_RUNTIME_TARGET", "").strip()
    else:
        target = options.runtimeTarget if options.mode == "remote" else ""
    if target:
        from emo_master.apps.designer.services.runtime_connection import connectCandidate

        if cancelled.is_set():
            return None
        timing.begin("runtime-channel")
        try:
            return connectCandidate(target, cancelled=cancelled)[0]
        except CancelledError:
            return None

    from emo_master.apps.runtime.grpc_server.service import RuntimeService

    if cancelled.is_set():
        return None
    if options is None:
        service = RuntimeService(startupProgress=timing.begin)
    else:
        service = RuntimeService(startupProgress=timing.begin, dbPath=options.dbPath,
                                 workspaceRoot=options.workspaceRoot, logDirectory=options.logDirectory)
    try:
        if cancelled.is_set():
            service.close()
            return None
        timing.begin("runtime-client")
        return RuntimeClient(runtimeService=service, ownedRuntimeService=service)
    except BaseException:
        service.close()
        raise
