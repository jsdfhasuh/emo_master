"""Persist Designer-origin diagnostics with the existing asynchronous log writer."""
import json
import os
from pathlib import Path
from uuid import uuid4
import logging
import time

from emo_master.apps.runtime.events.jsonl_writer import RuntimeJsonlLogWriter
from emo_master.apps.runtime.events.models import RuntimeEvent
from emo_master.apps.designer.startup_settings import initialStartupOptions


def designerLogDirectory(settings=None, *, options=None):
    configured = os.environ.get("EMO_DESIGNER_LOG_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    return (options or initialStartupOptions(settings)).dataRoot / "logs" / "designer"


class DesignerLogWriter(RuntimeJsonlLogWriter):
    filePrefix = "designer"

    def __init__(self, directory, **kwargs):
        self.sessionId = uuid4().hex
        self.sequence = 0
        super().__init__(directory, maximumFileBytes=8 * 1024 * 1024,
                         maximumTotalBytes=128 * 1024 * 1024, retentionDays=14, **kwargs)

    def acceptsEvent(self, event):
        return True

    def appendEntry(self, entry):
        self.sequence += 1
        return self.enqueue(RuntimeEvent(
            jobId=entry.jobId or self.sessionId, eventType=entry.eventType or "designer.log",
            message=entry.message, level=entry.level, code=entry.code,
            timestampMs=entry.timestampMs, sequence=self.sequence,
            payloadJson=json.dumps(entry.detailPayload(), ensure_ascii=False, default=str)))

    def record(self, eventType, message, *, level="INFO", payload=None):
        """Startup diagnostics use the same local record shape as window logs."""
        from emo_master.apps.designer.ui.log_dialog import StructuredLogEntry
        return self.appendEntry(StructuredLogEntry(
            timestampMs=int(time.time() * 1000), level=level, message=str(message),
            eventType=eventType, payload=payload or {}))

    def projectEvent(self, event):
        row = json.loads(event.payloadJson)
        return dict(row, schemaVersion="1.0", originApp="designer",
                    component=row.get("component") or (row.get("source") if row.get("source") in {
                        "editor", "flow_designer", "page_designer", "runtime_client"} else "application"),
                    runtimeInstanceId=row.get("runtimeInstanceId", ""),
                    designerSessionId=self.sessionId, localSequence=event.sequence)


def closeLogResources(resources):
    """Also retire readers/writers when Qt deletes a window without closeEvent."""
    for name in ("reader", "writer"):
        if name == "writer" and not resources.get("ownsWriter", True):
            continue
        resource = resources.get(name)
        if resource is not None:
            try:
                failure = resource.close()
                if failure:
                    logging.getLogger(__name__).error("Designer %s log resource close failed: %s", name, failure)
            except Exception:
                logging.getLogger(__name__).exception("Designer %s log resource close failed", name)
            resources[name] = None
