import json
import os
from pathlib import Path
import zipfile

import pytest

from emo_master.apps.operator_runtime.controller import ProductionRuntime
from emo_master.core.project.delivery_store import DirectoryOwner
from emo_master.core.project.runtime_package import buildRuntimePackage, installRuntimePackage
from tests.runtime.production_fixture import productionProject, saveDocument, waitFor


def testPortablePackagePreservesDeviceOutputPolicyAndCollectsExternalInput(tmp_path):
    source = tmp_path / "source"
    document = productionProject(source, autoStart=True)
    external = tmp_path / "external image.png"
    external.write_bytes((source / "input.png").read_bytes())
    document.resources.parameterBindings.clear()
    document.devices.bindings = {"camera": {"serial": "SITE-CAMERA"}}
    document.workflows["main"].nodes[1].params["imagePath"] = str(external)
    saveDocument(source, document)
    original = (source / "project.json").read_bytes()
    (source / "outputs").mkdir()
    (source / "outputs/private.csv").write_text("business history", encoding="utf-8")
    package = buildRuntimePackage(source, tmp_path / "packages")
    assert (source / "project.json").read_bytes() == original
    with zipfile.ZipFile(package) as archive:
        assert "outputs/private.csv" not in archive.namelist()
        assert any(name.startswith("runtime-assets/") for name in archive.namelist())
    destination = tmp_path / "moved engineering"
    installed = installRuntimePackage(package, destination)
    assert installed.devices == document.devices
    assert installed.production == document.production
    assert next(n.params for n in installed.workflows["main"].nodes if n.nodeId == "save")["outputPath"] == "outputs/result.png"
    assert not Path(installed.workflows["main"].nodes[1].params["imagePath"]).is_absolute()
    owner = ProductionRuntime(tmp_path / "data")
    try:
        owner.load(destination)
        lease = owner.projectOwner.stream
        owner.load(destination)
        assert owner.projectOwner.stream is lease
        job = owner.start()
        waitFor(lambda: any(e.eventType == "workflow.completed" for e in owner.runtime.eventStore.read(job)))
        assert (destination / "outputs/result.png").exists()
        owner.stop()
    finally:
        owner.close()


def testOfflineUpdatePreservesOutputsAndBacksUpConfiguration(tmp_path):
    source = tmp_path / "source"
    document = productionProject(source)
    destination = tmp_path / "installed"
    installRuntimePackage(buildRuntimePackage(source, tmp_path / "packages"), destination)
    (destination / "outputs").mkdir()
    (destination / "outputs/history.csv").write_bytes(b"do not replace")
    (destination / "notes.txt").write_bytes(b"local file")
    old = (destination / "project.json").read_bytes()
    document.production.autoStart = True
    saveDocument(source, document)
    installRuntimePackage(buildRuntimePackage(source, tmp_path / "packages"), destination)
    assert (destination / "outputs/history.csv").read_bytes() == b"do not replace"
    assert (destination / "notes.txt").read_bytes() == b"local file"
    assert any((backup / "project.json").read_bytes() == old
               for backup in tmp_path.glob(".runtime-backup-*") if (backup / "project.json").exists())


def testUpdateRequiresDirectoryOwnershipAndDifferentProjectUsesNewDirectory(tmp_path):
    source = tmp_path / "source"
    document = productionProject(source)
    package = buildRuntimePackage(source, tmp_path / "packages")
    destination = tmp_path / "installed"
    installRuntimePackage(package, destination)
    with DirectoryOwner(destination):
        with pytest.raises(RuntimeError, match="in use"):
            installRuntimePackage(package, destination)
    document.project.projectId = "another-product"
    saveDocument(source, document)
    with pytest.raises(ValueError, match="different project"):
        installRuntimePackage(buildRuntimePackage(source, tmp_path / "packages"), destination)


def testRunningRuntimeRefusesUpdateButStoppedOwnerCanApplyIt(tmp_path):
    source = tmp_path / "source"
    document = productionProject(source)
    package = buildRuntimePackage(source, tmp_path / "packages")
    destination = tmp_path / "installed"
    installRuntimePackage(package, destination)
    owner = ProductionRuntime(tmp_path / "data")
    try:
        owner.load(destination)
        job = owner.start()
        waitFor(lambda: any(e.eventType == "workflow.completed" for e in owner.runtime.eventStore.read(job)))
        with pytest.raises(ValueError, match="stop the current"):
            owner.installPackage(package)
        with pytest.raises(RuntimeError, match="in use"):
            installRuntimePackage(package, destination)
        owner.stop()
        document.production.cycleIntervalMs = 200
        saveDocument(source, document)
        owner.installPackage(buildRuntimePackage(source, tmp_path / "packages"))
        assert owner.settings.cycleIntervalMs == 200
        assert not owner.jobId and owner.status()["canStart"]
    finally:
        owner.close()


def testUpdateCanRepairMissingOldInputWithoutDeletingBusinessData(tmp_path):
    source = tmp_path / "source"
    productionProject(source)
    package = buildRuntimePackage(source, tmp_path / "packages")
    destination = tmp_path / "installed"
    installRuntimePackage(package, destination)
    (destination / "input.png").unlink()
    installRuntimePackage(package, destination)
    assert (destination / "input.png").exists()


def testNormalUpdateFailureRestoresReplacedResourcesAndConfig(tmp_path, monkeypatch):
    import emo_master.core.project.runtime_package as module
    source = tmp_path / "source"
    document = productionProject(source)
    destination = tmp_path / "installed"
    first = buildRuntimePackage(source, tmp_path / "packages")
    installRuntimePackage(first, destination)
    original = {name: (destination / name).read_bytes() for name in ("project.json", "input.png")}
    document.production.autoStart = True
    saveDocument(source, document)
    second = buildRuntimePackage(source, tmp_path / "packages")
    replace = module.os.replace
    def fail(source, target):
        if Path(target) == destination / "project.json" and ".runtime-stage-" in str(source):
            raise OSError("simulated disk failure")
        return replace(source, target)
    monkeypatch.setattr(module.os, "replace", fail)
    with pytest.raises(OSError, match="disk failure"):
        installRuntimePackage(second, destination)
    assert all((destination / name).read_bytes() == data for name, data in original.items())


@pytest.mark.parametrize("tamper", ["digest", "traversal", "case-alias", "undeclared", "plugin"])
def testBadPackageFailsBeforeReplacingInstalledProject(tmp_path, tamper):
    source = tmp_path / "source"
    productionProject(source)
    valid = buildRuntimePackage(source, tmp_path / "packages")
    destination = tmp_path / "installed"
    installRuntimePackage(valid, destination)
    original = (destination / "project.json").read_bytes()
    with zipfile.ZipFile(valid) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    if tamper == "digest":
        files["input.png"] += b"changed"
    elif tamper == "traversal":
        files["../escape"] = b"unsafe"
    elif tamper == "case-alias":
        files["INPUT.PNG"] = files["input.png"]
    elif tamper == "undeclared":
        files["extra.py"] = b"not declared"
    else:
        manifest = json.loads(files["manifest.json"])
        manifest["requiredOperators"]["vision.io.image_loader"] = "99.0.0"
        files["manifest.json"] = json.dumps(manifest).encode()
    invalid = tmp_path / "invalid.vxpkg"
    with zipfile.ZipFile(invalid, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    with pytest.raises((ValueError, KeyError)):
        installRuntimePackage(invalid, destination)
    assert (destination / "project.json").read_bytes() == original
    assert not (tmp_path / "escape").exists()


def testExporterRejectsOutputInsideSource(tmp_path):
    source = tmp_path / "source"
    productionProject(source)
    with pytest.raises(ValueError, match="outside"):
        buildRuntimePackage(source, source / "packages")


@pytest.mark.parametrize("winerror", [5, 32, 33])
def testUpdateWaitsForTransientWindowsReplacementDenial(tmp_path, monkeypatch, winerror):
    import emo_master.core.project.runtime_package as module
    source = tmp_path / "source"
    document = productionProject(source)
    destination = tmp_path / "installed"
    installRuntimePackage(buildRuntimePackage(source, tmp_path / "packages"), destination)
    original = (destination / "project.json").read_bytes()
    (destination / "outputs").mkdir()
    history = destination / "outputs/history.csv"
    history.write_bytes(b"site history")
    document.production.cycleIntervalMs = 200
    saveDocument(source, document)
    package = buildRuntimePackage(source, tmp_path / "packages")
    replace = module.os.replace
    attempts = []

    def temporaryDenial(source, target):
        if Path(target) == destination / "project.json" and ".runtime-stage-" in str(source):
            attempts.append((source, target))
            if len(attempts) == 1:
                if os.name == "nt" and winerror == 32:
                    # Exercise an actual non-delete-sharing reader on Windows;
                    # the other cases keep their exact synthetic error codes.
                    import ctypes
                    from ctypes import wintypes
                    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
                    create = kernel.CreateFileW
                    create.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                       ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
                    create.restype = wintypes.HANDLE
                    close = kernel.CloseHandle
                    close.argtypes = (wintypes.HANDLE,)
                    close.restype = wintypes.BOOL
                    handle = create(str(target), 0x80000000, 0x1 | 0x2, None, 3, 0, None)
                    assert handle != ctypes.c_void_p(-1).value, ctypes.get_last_error()
                    try:
                        with pytest.raises(PermissionError) as blocked:
                            replace(source, target)
                        assert blocked.value.winerror in {5, 32, 33}
                        raise blocked.value
                    finally:
                        assert close(handle)
                error = PermissionError(13, "Windows replacement denied while a file handle is active")
                error.winerror = winerror
                raise error
        return replace(source, target)

    monkeypatch.setattr(module.os, "replace", temporaryDenial)
    installed = installRuntimePackage(package, destination)
    assert len(attempts) == 2 and attempts[0] == attempts[1]
    assert installed.production.cycleIntervalMs == 200
    assert json.loads((destination / "project.json").read_text())["production"]["cycleIntervalMs"] == 200
    assert history.read_bytes() == b"site history"
    assert any((backup / "project.json").read_bytes() == original
               for backup in tmp_path.glob(".runtime-backup-*") if (backup / "project.json").exists())


@pytest.mark.parametrize("winerror, expectedAttempts", [(5, 5), (32, 5), (33, 5), (None, 1), (2, 1)])
def testPermanentReplacementDenialRemainsBoundedAndRestoresInputs(tmp_path, monkeypatch, winerror, expectedAttempts):
    import hashlib
    import emo_master.core.project.runtime_package as module
    source = tmp_path / "source"
    document = productionProject(source)
    destination = tmp_path / "installed"
    installRuntimePackage(buildRuntimePackage(source, tmp_path / "packages"), destination)
    original = {name: (destination / name).read_bytes() for name in ("project.json", "input.png")}
    # A valid PNG can carry trailing bytes; ensure rollback restores different
    # input bytes, rather than merely copying an identical resource twice.
    changedInput = original["input.png"] + b"updated package input"
    (source / "input.png").write_bytes(changedInput)
    document.resources.items["input"].sha256 = hashlib.sha256(changedInput).hexdigest()
    document.resources.items["input"].size = len(changedInput)
    document.production.cycleIntervalMs = 200
    saveDocument(source, document)
    package = buildRuntimePackage(source, tmp_path / "packages")
    replace = module.os.replace
    attempts = []
    rollbackAttempts = []
    error = PermissionError(13, "persistent permission denial")
    if winerror is not None:
        error.winerror = winerror

    def denied(source, target):
        if Path(target) == destination / "project.json" and ".runtime-stage-" in str(source):
            assert (destination / "input.png").read_bytes() == changedInput
            attempts.append((source, target))
            raise error
        if Path(target) == destination / "input.png" and ".runtime-backup-" in str(source):
            rollbackAttempts.append((source, target))
            if winerror == 5 and len(rollbackAttempts) == 1:
                occupied = PermissionError(13, "Windows reader still holds the rollback target")
                occupied.winerror = 32
                raise occupied
        return replace(source, target)

    monkeypatch.setattr(module.os, "replace", denied)
    with pytest.raises(PermissionError) as failure:
        installRuntimePackage(package, destination)
    assert failure.value is error
    assert len(attempts) == expectedAttempts
    assert len(rollbackAttempts) == (2 if winerror == 5 else 1)
    assert all((destination / name).read_bytes() == data for name, data in original.items())
