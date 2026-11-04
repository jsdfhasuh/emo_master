"""Application startup diagnostics, available before a Runtime service exists."""
import json
import time
from uuid import uuid4

from emo_master.apps.runtime.events.jsonl_writer import RuntimeJsonlLogWriter
from emo_master.apps.runtime.events.models import RuntimeEvent


class RuntimeApplicationLogWriter(RuntimeJsonlLogWriter):
    filePrefix = "runtime_app"

    def __init__(self, directory, *, runtimeInstanceId=None, **kwargs):
        self.runtimeInstanceId = runtimeInstanceId or uuid4().hex
        self.sequence = 0
        super().__init__(directory, **kwargs)

    def acceptsEvent(self, event):
        return True

    def record(self, eventType, message, *, level="INFO", payload=None):
        self.sequence += 1
        return self.enqueue(RuntimeEvent(
            jobId="", eventType=eventType, message=str(message), level=level,
            timestampMs=int(time.time() * 1000), sequence=self.sequence,
            payloadJson=json.dumps(payload or {}, ensure_ascii=False, default=str),
            originApp="runtime", component="application", runtimeInstanceId=self.runtimeInstanceId))
