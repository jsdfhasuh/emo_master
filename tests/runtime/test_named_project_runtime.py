"""Named project entrypoints through Runtime and package delivery."""
import zipfile

import pytest

from emo_master.apps.designer.state.project_store import createProjectSkeleton
from emo_master.apps.operator_runtime.controller import ProductionRuntime
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from emo_master.core.project.package_builder import buildPackage
from emo_master.core.project.runtime_package import buildRuntimePackage, installRuntimePackage
from tests.runtime.production_fixture import productionProject, waitFor


def testRuntimeMatchesExactNamedProjectAndRejectsAmbiguousDirectory(tmp_path):
    root = tmp_path / "projects"
    named, legacy = root / "inspection.emoproj", root / "project.json"
    createProjectSkeleton(named, "Named")
    createProjectSkeleton(legacy, "Legacy")
    service = RuntimeService(dbPath=tmp_path / "data/runtime.db", workspaceRoot=tmp_path / "jobs")
    try:
        reply = service.LoadProject(pb.LoadProjectRequest(project_path=str(named)), None)
        assert reply.ok, reply.message
        assert service.loadedDocument.project.name == "Named"
        assert service.loadedProjectFile == str(named)
        assert service.ValidateProject(pb.ValidateProjectRequest(project_id=str(named)), None).ok
        assert not service.ValidateProject(pb.ValidateProjectRequest(project_id=str(legacy)), None).ok
        session = service.OpenRunInspectionSession(pb.RunInspectionSessionRequest(project_id=str(named)), None)
        assert session.ok
        reply = service.LoadProject(pb.LoadProjectRequest(project_path=str(root)), None)
        assert not reply.ok and "multiple project files" in reply.message
        assert service.loadedProjectFile is None
    finally:
        service.close()


def testNamedProductionProjectRunsWithRelativeInputsAndOutputs(tmp_path):
    root = tmp_path / "\u4e2d\u6587\u5de5\u7a0b"
    source = tmp_path / "fixture"
    productionProject(source, mode="single")
    source.rename(root)
    named = root / "\u56fe\u7247\u68c0\u6d4b.emoproj"
    (root / "project.json").rename(named)
    original = named.read_bytes()
    owner = ProductionRuntime(tmp_path / "runtime-data")
    try:
        owner.load(named)
        assert owner.runtime.loadedProjectFile == str(named)
        job = owner.start()
        record = waitFor(lambda: (r if (r := owner.runtime.jobRepository.get(job)) and r.isTerminal else None))
        assert record.status == "COMPLETED", record.message
        assert (root / "outputs/result.png").exists()
        assert named.read_bytes() == original
    finally:
        owner.close()


def testProductionCannotOverwriteNamedProjectFile(tmp_path):
    root = tmp_path / "project"
    document = productionProject(root)
    named = root / "inspection.emoproj"
    (root / "project.json").rename(named)
    next(n for n in document.workflows["main"].nodes if n.nodeId == "save").params["outputPath"] = named.name
    named.write_text(document.model_dump_json(), encoding="utf-8")
    owner = ProductionRuntime(tmp_path / "runtime-data")
    try:
        with pytest.raises(ValueError, match="output overlaps"):
            owner.load(named)
    finally:
        owner.close()


def testNamedProjectExportAndOfflineUpdatePreserveSelectedName(tmp_path):
    root = tmp_path / "source"
    document = productionProject(root)
    named = root / "inspection.emoproj"
    (root / "project.json").rename(named)
    package = buildRuntimePackage(named, tmp_path / "packages")
    with zipfile.ZipFile(package) as archive:
        assert "project.json" in archive.namelist()
        assert not any(name.endswith(".emoproj") for name in archive.namelist())
    target = tmp_path / "target"
    installRuntimePackage(package, target)
    targetFile = target / "site.emoproj"
    (target / "project.json").rename(targetFile)
    original = targetFile.read_bytes()
    document.production.autoStart = True
    named.write_text(document.model_dump_json(), encoding="utf-8")
    update = buildRuntimePackage(named, tmp_path / "updates")
    installed = installRuntimePackage(update, target)
    assert installed.production.autoStart
    assert targetFile.exists() and not (target / "project.json").exists()
    assert any((backup / "site.emoproj").read_bytes() == original
               for backup in tmp_path.glob(".runtime-backup-*") if (backup / "site.emoproj").exists())


def testLegacyPackageBuilderAcceptsNamedSourceWithoutDuplicatingIt(tmp_path):
    named = tmp_path / "project" / "test.emoproj"
    createProjectSkeleton(named, "Test")
    package = buildPackage(named, tmp_path / "packages")
    with zipfile.ZipFile(package) as archive:
        assert "project.json" in archive.namelist()
        assert "test.emoproj" not in archive.namelist()
