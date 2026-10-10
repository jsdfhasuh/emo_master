"""Synthetic local image fixture, never a site equipment acceptance project."""
import time
from pathlib import Path

from emo_master.core.project.migration import migrateProjectPayload
from emo_master.core.project.models import ProjectDocument
from examples.runtime_pages_p2 import sampleProject


def productionProject(root, *, mode="continuous", autoStart=False, interval=100, save=True, overwrite=True):
    root.mkdir(parents=True, exist_ok=True)
    payload = migrateProjectPayload(sampleProject(root).model_dump(), enableProduction=True)
    payload["production"].update(mode=mode, autoStart=autoStart, cycleIntervalMs=interval)
    if save:
        workflow = payload["workflows"]["main"]
        workflow["nodes"].insert(-1, {"nodeId": "save", "operatorId": "vision.io.image_saver",
            "params": {"outputPath": "outputs/result.png", "overwrite": overwrite}})
        workflow["edges"].append(dict(fromNode="load", fromPort="image", toNode="save", toPort="image"))
    document = ProjectDocument.model_validate(payload)
    (root / "project.json").write_text(document.model_dump_json(indent=2), encoding="utf-8")
    return document


def waitFor(predicate, seconds=20):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(.02)
    raise AssertionError("production Runtime deadline")


def saveDocument(root: Path, document):
    (root / "project.json").write_text(document.model_dump_json(indent=2), encoding="utf-8")
