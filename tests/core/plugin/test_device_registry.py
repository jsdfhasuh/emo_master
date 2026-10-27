from copy import deepcopy
import json
from pathlib import Path

import pytest

from emo_master.core.plugin.registry import PluginRegistry
from emo_master.core.plugin.validator import validateManifestFields


ROOT = Path(__file__).resolve().parents[3]


def declaration():
    return json.loads((ROOT / "examples/device_plugin/device_types/simulated_camera.json").read_text())


def write(root, name, payload):
    path = root / name / "device_types" / "camera.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def testIndependentDeviceDiscoveryDoesNotImportOperators(tmp_path, monkeypatch):
    from emo_master.core.plugin import registry
    write(tmp_path, "first", declaration())
    monkeypatch.setattr(registry, "loadOperatorClass", lambda *_: pytest.fail("operator import"))
    result = PluginRegistry("0.6.1").scanRoots(iter([tmp_path]))
    assert list(result.deviceTypes) == ["device.example.simulated_camera"]
    assert not result.activeOperators and not result.rejectedDeviceTypes


def testIdenticalTypesDeduplicateButConflictsPoisonOnlyOneType(tmp_path):
    raw = declaration()
    write(tmp_path, "first", raw)
    write(tmp_path, "second", raw)
    scan = PluginRegistry("0.6.1")
    active, rejected = scan.scanDeviceTypes([tmp_path, tmp_path])
    assert len(active) == 1 and not rejected
    changed = deepcopy(raw)
    changed["displayName"] = "Conflicting camera"
    write(tmp_path, "third", changed)
    other = deepcopy(raw)
    other["deviceTypeId"] = "device.other"
    write(tmp_path, "fourth", other)
    active, rejected = scan.scanDeviceTypes([tmp_path])
    assert list(active) == ["device.other"]
    assert list(rejected) == [raw["deviceTypeId"]]


@pytest.mark.parametrize("change", ["version", "path", "dynamic", "unknown", "schema"])
def testInvalidDeclarationIsIsolated(tmp_path, change):
    raw = declaration()
    if change == "version":
        raw["minCoreVersion"] = "9.0"
        raw["maxCoreVersion"] = "9.x"
    elif change == "path":
        raw["overridePaths"].append("connection.source")
    elif change == "dynamic":
        raw["dynamicPaths"].append("not-a-parameter")
    elif change == "schema":
        raw["connectionSchema"]["additionalProperties"] = True
    else:
        raw["runtimeHandle"] = "not metadata"
    write(tmp_path, "bad", raw)
    other = declaration()
    other["deviceTypeId"] = "device.valid"
    write(tmp_path, "good", other)
    active, rejected = PluginRegistry("0.6.1").scanDeviceTypes([tmp_path])
    assert list(active) == ["device.valid"]
    assert len(rejected) == 1


def testMalformedJsonDoesNotBlockOtherTypes(tmp_path):
    bad = write(tmp_path, "bad", {})
    bad.write_text("{", encoding="utf-8")
    write(tmp_path, "good", declaration())
    active, rejected = PluginRegistry("0.6.1").scanDeviceTypes([tmp_path])
    assert len(active) == len(rejected) == 1


def testOperatorRequirementsAreOptionalAndStrict():
    payload = json.loads((ROOT / "src/emo_master/plugins/builtins/plc_slmp_read/manifest.json").read_text(encoding="utf-8"))
    payload.pop("deviceRequirements")
    assert 'deviceBindingSince' not in payload
    manifest, errors = validateManifestFields(payload)
    assert not errors and manifest.deviceRequirements == {}
    payload["deviceRequirements"] = {"plc": {"deviceTypeId": "device.plc.mitsubishi_slmp"}}
    manifest, errors = validateManifestFields(payload)
    assert not errors and manifest.deviceRequirements["plc"].deviceTypeId == "device.plc.mitsubishi_slmp"
    payload["deviceRequirements"]["plc"]["host"] = "not allowed"
    manifest, errors = validateManifestFields(payload)
    assert manifest is None and errors[0].code == "E_DEVICE_REQUIREMENT"


def testRootReadFailureDoesNotAbortOtherDeviceRoots(tmp_path, monkeypatch):
    write(tmp_path / "good", "plugin", declaration())
    original = Path.glob

    def glob(path, pattern):
        if path.name == "bad":
            raise OSError("unreadable root")
        return original(path, pattern)

    monkeypatch.setattr(Path, "glob", glob)
    active, rejected = PluginRegistry("0.6.1").scanDeviceTypes([tmp_path / "bad", tmp_path / "good"])
    assert len(active) == len(rejected) == 1
