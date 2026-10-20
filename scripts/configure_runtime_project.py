"""Explicitly persist the four project-owned production settings."""
from pathlib import Path
import argparse
import json
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from emo_master.core.project.delivery_store import atomicJson
from emo_master.core.project.migration import loadProjectPayload, migrateProjectPayload
from emo_master.core.project.models import ProjectDocument, ProductionSettings


def main(arguments=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path)
    parser.add_argument("--auto-start", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--mode", choices=["single", "continuous"])
    parser.add_argument("--cycle-interval-ms", type=int)
    parser.add_argument("--inputs-json", help="JSON object of entry workflow inputs")
    args = parser.parse_args(arguments)
    path = args.project.resolve()
    if path.is_dir():
        path /= "project.json"
    payload = migrateProjectPayload(loadProjectPayload(path), enableProduction=True)
    document = ProjectDocument.model_validate(payload)
    settings = document.production.model_dump()
    for key, value in [("autoStart", args.auto_start), ("mode", args.mode),
                       ("cycleIntervalMs", args.cycle_interval_ms)]:
        if value is not None:
            settings[key] = value
    if args.inputs_json is not None:
        settings["inputs"] = json.loads(args.inputs_json)
    document.production = ProductionSettings.model_validate(settings)
    shutil.copy2(path, path.with_suffix(".json.bak"))
    atomicJson(path, document.model_dump(mode="json"))
    print(f"Updated {path}: mode={document.production.mode}, "
          f"autoStart={document.production.autoStart}, intervalMs={document.production.cycleIntervalMs}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
