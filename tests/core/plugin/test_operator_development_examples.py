"""Execute the developer guide's examples against the current plugin contracts."""
from copy import deepcopy
import json
from pathlib import Path
import sys
from types import ModuleType

import pytest

from emo_master import __version__
from emo_master.apps.designer.state.project_store import loadProjectDocument, saveProject
from emo_master.apps.designer.state.workflow_store import WorkflowStore
from emo_master.apps.runtime.devices.drivers import validateDriver
from emo_master.apps.runtime.workflow.cancellation import CancellationToken
from emo_master.apps.runtime.workflow.context import RunContext
from emo_master.apps.runtime.workflow.runner import WorkflowRunner
from emo_master.core.contracts.port_types import normalizePortType
from emo_master.core.plugin.registry import PluginRegistry
from emo_master.core.project.models import ProjectDocument
from emo_master.core.workflow.compiler import WorkflowCompiler
from emo_master.core.workflow.errors import WorkflowCompileError


ROOT = Path(__file__).resolve().parents[3]
GUIDE = ROOT / "docs/operator-development-guide.md"


def example(name, language):
    text = GUIDE.read_text(encoding="utf-8")
    marker = f"<!-- example: {name} -->\n```{language}\n"
    assert text.count(marker) == 1, f"Missing or ambiguous example: {name}"
    return text.split(marker, 1)[1].split("\n```", 1)[0]


def writeJson(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def scaleExample(tmp_path, monkeypatch):
    manifest = json.loads(example("scale-manifest", "json"))
    moduleName, _ = manifest["entry"].split(":")
    module = ModuleType(moduleName)
    monkeypatch.setitem(sys.modules, moduleName, module)
    exec(compile(example("scale-operator", "python"), str(GUIDE), "exec"), module.__dict__)
    root = tmp_path / "plugin"
    writeJson(root / "manifest.json", manifest)
    result = PluginRegistry(__version__).scan(root)
    assert not result.rejectedOperators and not result.rejectedDeviceTypes
    assert set(result.activeOperators) == {manifest["operatorId"]}
    return manifest, result.activeOperators


def projectWithOperator(manifest, params):
    payload = WorkflowStore().toPayload("Documentation Example")
    assert payload["schemaVersion"] == "2.7"
    assert payload["devices"]["schemaVersion"] == "1.0"
    assert "communication" not in payload
    payload["dependencies"]["operators"] = [
        {"operatorId": manifest["operatorId"], "version": manifest["version"]}
    ]
    workflow = payload["workflows"]["main"]
    workflow["inputs"] = deepcopy(manifest["inputPorts"])
    workflow["outputs"] = deepcopy(manifest["outputPorts"])
    entry, exitNode = workflow["nodes"]
    inputTypes = {key: normalizePortType(spec) for key, spec in manifest["inputPorts"].items()}
    outputTypes = {key: normalizePortType(spec) for key, spec in manifest["outputPorts"].items()}
    entry["outputPorts"] = inputTypes.copy()
    exitNode["inputPorts"] = outputTypes.copy()
    node = dict(nodeId="example", kind="operator", operatorId=manifest["operatorId"],
                displayName=manifest["displayName"], params=params,
                paramSchema=deepcopy(manifest["paramSchema"]),
                inputPorts=inputTypes, outputPorts=outputTypes)
    workflow["nodes"] = [entry, node, exitNode]
    workflow["edges"] = [
        dict(fromNode=entry["nodeId"], fromPort=port, toNode="example", toPort=port)
        for port in manifest["inputPorts"]
    ] + [
        dict(fromNode="example", fromPort=port, toNode=exitNode["nodeId"], toPort=port)
        for port in manifest["outputPorts"]
    ]
    return payload, node


def saveAndReload(payload, target):
    document = ProjectDocument.model_validate(payload)
    saveProject(target, document.model_dump(mode="json"))
    reloaded = loadProjectDocument(target)
    assert reloaded == document
    return reloaded


def testGuideScaleUnitExample(scaleExample):
    namespace = {}
    exec(compile(example("scale-test", "python"), str(GUIDE), "exec"), namespace)
    namespace["testScale"]()


def testGuideScaleRegistersSavesCompilesAndRuns(tmp_path, scaleExample):
    manifest, registry = scaleExample
    payload, _ = projectWithOperator(manifest, {"factor": 4.0})
    document = saveAndReload(payload, tmp_path / "scale.emoproj")
    assert document.devices.definitions == {}
    compiled = WorkflowCompiler(operatorRegistry=registry).compile(document)
    result = WorkflowRunner(compiled, registry).run(
        "main", {"value": 3.0}, RunContext.root("guide-scale", "main", str(tmp_path)),
        CancellationToken())
    assert result.outputs == {"value": 12.0}


def testGuideTickerDescriptorAndBinding(tmp_path):
    declaration = json.loads(example("ticker-device", "json"))
    manifest = json.loads((ROOT / "examples/device_service/manifest.json").read_text(encoding="utf-8"))
    manifest.update(json.loads(example("ticker-requirement", "json")))
    root = tmp_path / "plugin"
    writeJson(root / "manifest.json", manifest)
    writeJson(root / "device_types/ticker.json", declaration)
    result = PluginRegistry(__version__).scan(root)
    assert not result.rejectedOperators and not result.rejectedDeviceTypes
    assert set(result.activeOperators) == {manifest["operatorId"]}
    assert set(result.deviceTypes) == {declaration["deviceTypeId"]}
    assert validateDriver(result.deviceTypes[declaration["deviceTypeId"]]).__name__ == "Ticker"

    payload, node = projectWithOperator(manifest, {"reject": False})
    payload["devices"]["definitions"]["ticker-01"] = json.loads(example("ticker-definition", "json"))
    node.update(json.loads(example("ticker-binding", "json")))
    document = saveAndReload(payload, tmp_path / "ticker.emoproj")
    compiler = WorkflowCompiler(result.activeOperators, deviceTypes=result.deviceTypes)
    compiled = compiler.compile(document)
    plan = compiled.workflows["main"].nodeById["example"].devicePlans["service"]
    assert plan["deviceId"] == "ticker-01" and plan["selectors"] == {}
    assert plan["declaration"]["deviceTypeId"] == "device.example.ticker"
    node["deviceBindings"] = {}
    with pytest.raises(WorkflowCompileError, match="device slots do not match"):
        compiler.compile(ProjectDocument.model_validate(payload))
