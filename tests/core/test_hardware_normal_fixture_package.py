"""Structural/resource acceptance only: no hardware/model inference is started."""
import hashlib
import json
from pathlib import Path
import zipfile

import pytest

from emo_master import __version__
from emo_master.apps.runtime.workflow.cancellation import CancellationToken
from emo_master.apps.runtime.workflow.context import RunContext
from emo_master.apps.runtime.workflow.runner import WorkflowRunner
from emo_master.core.plugin.registry import PluginRegistry
from emo_master.core.project.models import ProjectDocument
from emo_master.core.project.runtime_directory import prepareRuntimeProject
from emo_master.core.project.runtime_package import buildRuntimePackage, installRuntimePackage
from emo_master.core.workflow.compiler import WorkflowCompiler


ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "examples/hardware_trigger_normal_fixture"


def sourceProject(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    payload = json.loads((EXAMPLE / "project.json").read_text(encoding="utf-8"))
    for station in (1, 2):
        filename = f"station{station}_reference.txt"
        (source / filename).write_bytes((EXAMPLE / filename).read_bytes())
    # These are distinct external model-boundary placeholders. Packaging checks
    # files/paths/hashes; it cannot establish ONNX or old-algorithm equivalence.
    for model in ("a", "b", "holes"):
        (source / f"{model}.onnx").write_bytes(f"fixture model boundary: {model}".encode())
    (source / "project.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    scan = PluginRegistry(__version__).scan(ROOT / "src/emo_master/plugins")
    assert not scan.rejectedOperators
    return source, ProjectDocument.model_validate(payload), scan.activeOperators


def testFixtureSaveReloadExportRelocationAndCoordinateAdmission(tmp_path):
    source, document, registry = sourceProject(tmp_path)
    original = (source / "project.json").read_bytes()
    before = document.model_dump()
    (source / "runtime.sqlite3").write_bytes(b"must not export live state")
    (source / "unrelated-secret.txt").write_bytes(b"must not export undeclared unrelated files")
    package = buildRuntimePackage(source, tmp_path / "packages", registry=registry)
    assert (source / "project.json").read_bytes() == original
    assert document.model_dump() == before
    with zipfile.ZipFile(package) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        exported = ProjectDocument.model_validate_json(archive.read("project.json"))
        assert exported.schemaVersion == "2.4"
        assert exported.globalVariables == document.globalVariables
        assert set(exported.workflows) == set(document.workflows)
        assets = [name for name in archive.namelist() if name.startswith("runtime-assets/")]
        assert len(assets) == 5
        assert "runtime.sqlite3" not in archive.namelist()
        assert "unrelated-secret.txt" not in archive.namelist()
        assert all(manifest["files"][name]["sha256"] == hashlib.sha256(archive.read(name)).hexdigest()
                   for name in assets)
    moved = tmp_path / "moved engineering"
    installed = installRuntimePackage(package, moved, registry=registry)
    assert installed.production == document.production
    assert installed.runtime == document.runtime
    prepared = prepareRuntimeProject(installed, moved, registry)
    compiled = WorkflowCompiler(registry).compile(prepared)
    for station, expected in ((1, (10.0, 20.0)), (2, (-10.0, 15.0))):
        root = f"station{station}"
        runner = WorkflowRunner(compiled, registry, retainOperators=True)
        context = RunContext.root(f"job-{station}", root, str(moved), projectId=document.project.projectId)
        try:
            runner.prepareResources(root, context, CancellationToken())
            # Resource admission opens no camera, PLC, TCP or inference operator.
            assert not runner._lifecycleOperators
            contents = list(runner.coordinateSnapshots._contents.values())
            assert contents and all(content.rows == (expected,) for content in contents)
            for workflow in compiled.workflows.values():
                for node in workflow.nodes:
                    if node.operatorId == "vision.io.coordinate_reader" and node.params["filePath"].startswith(str(moved)):
                        path = Path(node.params["filePath"])
                        if any(content.sha256 == hashlib.sha256(path.read_bytes()).hexdigest() for content in contents):
                            frozen = runner.coordinateSnapshots.get(path, node.params)
                            path.write_bytes(b"777 888\n")
                            runner.prepareResources(root, context, CancellationToken())
                            assert runner.coordinateSnapshots.get(path, node.params) == frozen
                            # Restore the relocated resource for the other readers
                            # and station, not the running session's snapshot.
                            path.write_bytes((EXAMPLE / f"station{station}_reference.txt").read_bytes())
        finally:
            runner.closeSession(context)


def testFixtureExportRejectsMissingTxtWithoutWritingPackage(tmp_path):
    source, _, registry = sourceProject(tmp_path)
    (source / "station2_reference.txt").unlink()
    output = tmp_path / "packages"
    with pytest.raises(ValueError, match="input file not found"):
        buildRuntimePackage(source, output, registry=registry)
    assert not output.exists()
