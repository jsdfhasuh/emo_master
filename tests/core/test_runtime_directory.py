from copy import deepcopy
from pathlib import Path

import pytest

from emo_master import __version__
from emo_master.apps.designer.state.project_edit_session import ProjectEditSession
from emo_master.apps.designer.state.workflow_store import WorkflowStore
from emo_master.core.plugin.registry import PluginRegistry
from emo_master.core.project.migration import migrateProjectPayload
from emo_master.core.project.models import ProjectDocument
from emo_master.core.project.runtime_directory import _resolveFiles, prepareRuntimeProject
from tests.runtime.production_fixture import productionProject


@pytest.fixture(scope="module")
def registry():
    root = Path(__file__).resolve().parents[2] / "src/emo_master/plugins/builtins"
    return PluginRegistry(coreVersion=__version__).scanRoots((root,)).activeOperators


def testExplicitProductionUpgradeOldShapeAndDesignerRoundtrip(tmp_path):
    document = productionProject(tmp_path / "project", autoStart=True)
    payload = document.model_dump()
    store = WorkflowStore(payload)
    assert store.toPayload()["production"] == payload["production"]
    session = ProjectEditSession(payload)
    assert not session.dirty
    assert session.document().schemaVersion == "2.3"
    assert migrateProjectPayload(payload, enablePresentation=True) == payload
    old = deepcopy(payload)
    old.update(schemaVersion="2.1")
    for key in ("production", "presentation", "resources"):
        old.pop(key)
    assert migrateProjectPayload(old) == old
    assert "production" not in ProjectDocument.model_validate(old).model_dump()
    upgraded = migrateProjectPayload(old, enableProduction=True)
    assert upgraded["production"] == dict(autoStart=False, mode="single", cycleIntervalMs=100, inputs={})
    assert upgraded["schemaVersion"] == "2.3"


@pytest.mark.parametrize("change", [dict(cycleIntervalMs=0), dict(mode="loop"), dict(autoStart="yes"), dict(retry=True)])
def testInvalidProductionConfigurationFailsAtBoundary(tmp_path, change):
    payload = productionProject(tmp_path / "project").model_dump()
    payload["production"].update(change)
    with pytest.raises(ValueError):
        migrateProjectPayload(payload)


def testMovedUnicodeProjectResolvesPathsNotCwdAndLeavesSourceUntouched(tmp_path, registry, monkeypatch):
    original = tmp_path / "original"
    document = productionProject(original)
    destination = tmp_path / "工程 现场"
    original.rename(destination)
    source = (destination / "project.json").read_bytes()
    monkeypatch.chdir(tmp_path)
    prepared = prepareRuntimeProject(document, destination, registry)
    nodes = {node.nodeId: node for node in prepared.workflows["main"].nodes}
    assert nodes["load"].params["imagePath"] == str(destination / "input.png")
    assert nodes["save"].params["outputPath"] == str(destination / "outputs/result.png")
    assert "imagePath" not in document.workflows["main"].nodes[1].params
    assert (destination / "project.json").read_bytes() == source


@pytest.mark.parametrize("target", ["input.png", "project.json", "runtime/state.sqlite3", "runtime/jobs/output.png"])
def testOutputCannotOverwriteInputsProjectOrRuntime(tmp_path, registry, target):
    root = tmp_path / "project"
    document = productionProject(root)
    node = next(n for n in document.workflows["main"].nodes if n.nodeId == "save")
    node.params["outputPath"] = target
    with pytest.raises(ValueError, match="output overlaps"):
        prepareRuntimeProject(document, root, registry, protectedPaths=(root / "runtime",))


def testAbsoluteFilePathsRetainExplicitMeaning(tmp_path, registry):
    root = tmp_path / "project"
    document = productionProject(root)
    outside = tmp_path / "explicit.png"
    node = next(n for n in document.workflows["main"].nodes if n.nodeId == "save")
    node.params["outputPath"] = str(outside)
    prepared = prepareRuntimeProject(document, root, registry)
    assert next(n for n in prepared.workflows["main"].nodes if n.nodeId == "save").params["outputPath"] == str(outside)


def testMissingAndModifiedResourceFailsBeforeExecution(tmp_path, registry):
    root = tmp_path / "project"
    document = productionProject(root)
    (root / "input.png").unlink()
    with pytest.raises(ValueError, match="resource missing"):
        prepareRuntimeProject(document, root, registry)
    (root / "input.png").write_bytes(b"bad")
    with pytest.raises(ValueError, match="size mismatch"):
        prepareRuntimeProject(document, root, registry)


def testFileArrayMetadataAndOptionalBlankFileHaveNoPathGuessing(tmp_path):
    (tmp_path / "input.txt").write_text("data", encoding="utf-8")
    fileSchema = dict(type="string", xWidget="file", xFileMode="open")
    schema = dict(type="object", properties={"files": dict(type="array", items=fileSchema),
                                           "optional": fileSchema})
    value = dict(files=["input.txt"], optional="", address="192.168.1.10")
    inputs, outputs = set(), []
    _resolveFiles(value, schema, tmp_path, inputs, outputs, "node")
    assert value == dict(files=[str(tmp_path / "input.txt")], optional="", address="192.168.1.10")
    assert inputs == {tmp_path / "input.txt"} and not outputs


def testLegacyPublishersAndTestPackagesDoNotAcceptProductionSchema(tmp_path):
    from emo_master.core.project.package_builder import buildPackage, buildPageTestPackage
    root = tmp_path / "project"
    document = productionProject(root, save=False)
    with pytest.raises(ValueError, match="publication requires"):
        buildPackage(root, tmp_path / "packages")
    with pytest.raises(ValueError, match="2.2 pages required"):
        buildPageTestPackage(document, root, tmp_path / "packages")
