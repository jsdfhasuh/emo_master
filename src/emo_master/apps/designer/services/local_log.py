"""Persist Designer-origin diagnostics with the existing asynchronous log writer."""
import json
import os
from pathlib import Path
from uuid import uuid4
import logging

from emo_master.apps.runtime.events.jsonl_writer import RuntimeJsonlLogWriter
from emo_master.apps.runtime.events.models import RuntimeEvent
from emo_master.apps.designer.startup_settings import initialStartupOptions


def designerLogDirectory(settings):
    configured = os.environ.get("EMO_DESIGNER_LOG_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    return initialStartupOptions(settings).dataRoot / "logs" / "designer"


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

    def projectEvent(self, event):
        return dict(json.loads(event.payloadJson), schemaVersion="1.0",
                    designerSessionId=self.sessionId, localSequence=event.sequence)


def closeLogResources(resources):
    """Also retire readers/writers when Qt deletes a window without closeEvent."""
    for name in ("reader", "writer"):
        resource = resources.get(name)
        if resource is not None:
            try:
                failure = resource.close()
                if failure:
                    logging.getLogger(__name__).error("Designer %s log resource close failed: %s", name, failure)
            except Exception:
                logging.getLogger(__name__).exception("Designer %s log resource close failed", name)
            resources[name] = None
