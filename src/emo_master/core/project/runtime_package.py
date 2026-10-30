"""Portable production projects and offline, output-preserving directory updates."""
from __future__ import annotations

from contextlib import nullcontext
import hashlib
import os
from pathlib import Path
import shutil
import stat
import tempfile
import time
from uuid import uuid4
import zipfile

from emo_master import __version__
from emo_master.core.presentation.validation import validateBindings
from emo_master.core.project.delivery_store import DirectoryOwner
from emo_master.core.project.migration import loadProjectPayload, migrateProjectPayload
from emo_master.core.project.models import ProjectDocument
from emo_master.core.project.files import projectFiles, resolveProjectFile
from emo_master.core.project.runtime_directory import prepareRuntimeProject
from emo_master.core.project.device_resources import deviceDeclaration, trustedDeviceTypes
from emo_master.core.project.snapshots import _inside, canonicalJson
from emo_master.core.project.test_delivery import packagePath, readJson, trustedRegistry
from emo_master.core.workflow.compiler import WorkflowCompiler


FORMAT = "emo-runtime-project-1"
MAX_TOTAL = 16 * 1024 ** 3
MAX_METADATA = 8 * 1024 ** 2
MAX_ENTRIES = 10000
RESERVED = {"project.json", "manifest.json", ".delivery.lock"}


def _replaceInstalledFile(source: Path, target: Path) -> None:
    # Windows may deny replacement while a short-lived reader holds the target
    # without delete sharing. Retry only this atomic operation, never extraction,
    # backup, or the entire update. Permanent denial still reaches rollback.
    for attempt in range(5):
        try:
            os.replace(source, target)
            return
        except PermissionError as error:
            if getattr(error, "winerror", None) not in {5, 32, 33} or attempt == 4:
                raise
            time.sleep(.05 * 2 ** attempt)


def _digest(stream, target=None):
    digest, size = hashlib.sha256(), 0
    for block in iter(lambda: stream.read(1024 * 1024), b""):
        size += len(block)
        if size > MAX_TOTAL:
            raise ValueError("runtime package file exceeds 16 GiB")
        digest.update(block)
        if target is not None:
            target.write(block)
    return dict(size=size, sha256=digest.hexdigest())


def _files(value, schema, visit):
    if schema.get("xWidget") == "file" and isinstance(value, str) and value:
        return visit(value, schema.get("xFileMode"))
    if isinstance(value, dict):
        for key, child in schema.get("properties", {}).items():
            if key in value:
                value[key] = _files(value[key], child, visit)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            value[index] = _files(item, schema.get("items", {}), visit)
    return value


def _validate(document, root, registry, deviceTypes=None, *, portable=False):
    _devices(document, deviceTypes)
    prepared = prepareRuntimeProject(document, root, registry, deviceTypes=deviceTypes)
    if portable:
        def validateDeviceInput(raw, mode):
            if mode == "directory":
                raise ValueError("packaged device input directories are unsupported")
            if mode == "open":
                path = Path(raw).resolve()
                if not path.is_relative_to(root.resolve()):
                    raise ValueError("packaged device input escapes package root")
                _inside(root, path.relative_to(root.resolve()).as_posix())
            return raw
        _deviceFiles(prepared, deviceTypes, validateDeviceInput)
    WorkflowCompiler(operatorRegistry=registry, deviceTypes=deviceTypes).compile(prepared)
    issues = validateBindings(prepared, {key: item.manifest for key, item in registry.items()})
    if issues:
        raise ValueError("project page bindings invalid: " + "; ".join(issue.message for issue in issues))
    return prepared


def _operators(document, registry):
    ids = {node.operatorId for flow in document.workflows.values() for node in flow.nodes if node.kind == "operator"}
    from emo_master.core.project.dependencies import requireInstalled
    for key in ids:
        descriptor = registry.get(key)
        requireInstalled(key, None, descriptor.manifest.version if descriptor else None,
            kind='算子', fieldPath='dependencies.operators.'+key, projectId=document.project.projectId)
    return {key: registry[key].manifest.version for key in sorted(ids)}


def _devices(document, deviceTypes):
    declarations = {value.deviceTypeId: deviceDeclaration(value, deviceTypes)
                    for value in document.devices.definitions.values()}
    return {key: dict(version=value.version, sha256=hashlib.sha256(
        canonicalJson(value.model_dump()).encode()).hexdigest()) for key, value in sorted(declarations.items())}


def _deviceFiles(document, deviceTypes, visit):
    for definition in document.devices.definitions.values():
        declaration = deviceDeclaration(definition, deviceTypes)
        for section, schema in (("connection", declaration.connectionSchema), ("defaults", declaration.defaultsSchema)):
            setattr(definition, section, _files(getattr(definition, section), schema, visit))
    for flow in document.workflows.values():
        for node in flow.nodes:
            for slot, binding in node.deviceBindings.items():
                declaration = deviceDeclaration(document.devices.definitions[binding.deviceId], deviceTypes)
                if slot in node.deviceOverrides:
                    node.deviceOverrides[slot] = _files(node.deviceOverrides[slot], declaration.defaultsSchema, visit)


def buildRuntimePackage(projectDir: Path, outputDir: Path, *, registry=None, deviceTypes=None) -> Path:
    registry = trustedRegistry() if registry is None else registry
    deviceTypes = trustedDeviceTypes() if deviceTypes is None else deviceTypes
    projectFile = resolveProjectFile(Path(projectDir).resolve(strict=True))
    root = projectFile.parent
    outputDir = Path(outputDir).resolve()
    if outputDir.is_relative_to(root):
        raise ValueError("package output must be outside the engineering project")
    document = ProjectDocument.model_validate(migrateProjectPayload(loadProjectPayload(projectFile), enableProduction=True))
    prepared = _validate(document, root, registry, deviceTypes)
    assert document.resources is not None
    sources = {item.path: _inside(root, item.path) for item in document.resources.items.values()}
    sourceNames = {path.resolve(): name for name, path in sources.items()}
    def collect(raw, mode):
        if mode != "open":
            return raw
        path = Path(raw)
        if path not in sourceNames:
            with path.open("rb") as stream:
                digest = _digest(stream)["sha256"]
            suffix = path.suffix if path.suffix.isascii() and path.suffix[1:].isalnum() else ".bin"
            name = f"runtime-assets/{digest}{suffix}"
            sources[name] = path
            sourceNames[path] = name
        return sourceNames[path]
    for wid, flow in document.workflows.items():
        original = {node.nodeId: node for node in flow.nodes}
        for node in prepared.workflows[wid].nodes:
            if node.kind == "operator":
                portable = _files(node.params, registry[node.operatorId].manifest.paramSchema, collect)
                # Only input paths change. Keep the project's device and output values verbatim.
                targetNode = original[node.nodeId]
                targetNode.params = _replaceInputs(targetNode.params, portable, registry[node.operatorId].manifest.paramSchema)
    def collectDevice(raw, mode):
        if mode == "directory":
            raise ValueError("runtime packages require explicit files, not device input directories")
        return collect(raw, mode)
    _deviceFiles(prepared, deviceTypes, collectDevice)
    for deviceId, definition in document.devices.definitions.items():
        declaration = deviceDeclaration(definition, deviceTypes)
        for section, schema in (("connection", declaration.connectionSchema), ("defaults", declaration.defaultsSchema)):
            setattr(definition, section, _replaceInputs(getattr(definition, section),
                    getattr(prepared.devices.definitions[deviceId], section), schema))
    for wid, flow in document.workflows.items():
        preparedNodes = {node.nodeId: node for node in prepared.workflows[wid].nodes}
        for node in flow.nodes:
            for slot, binding in node.deviceBindings.items():
                if slot in node.deviceOverrides:
                    declaration = deviceDeclaration(document.devices.definitions[binding.deviceId], deviceTypes)
                    node.deviceOverrides[slot] = _replaceInputs(node.deviceOverrides[slot],
                        preparedNodes[node.nodeId].deviceOverrides[slot], declaration.defaultsSchema)
    metadata = canonicalJson(document.model_dump(mode="json")).encode()
    if len(metadata) > MAX_METADATA or len(sources) + 2 > MAX_ENTRIES:
        raise ValueError("runtime package metadata or file count limit")
    outputDir.mkdir(parents=True, exist_ok=True)
    destination = outputDir / ("runtime-" + uuid4().hex + ".vxpkg")
    temporary = destination.with_suffix(".partial")
    try:
        checksums, total = {}, len(metadata)
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
            for name, path in sources.items():
                packagePath(name)
                if name.casefold() in RESERVED:
                    raise ValueError("resource uses reserved package path")
                with path.open("rb") as source, archive.open(name, "w", force_zip64=True) as outputStream:
                    checksums[name] = _digest(source, outputStream)
                total += checksums[name]["size"]
                if total > MAX_TOTAL:
                    raise ValueError("runtime package exceeds 16 GiB")
            checksums["project.json"] = dict(size=len(metadata), sha256=hashlib.sha256(metadata).hexdigest())
            archive.writestr("project.json", metadata)
            manifest = dict(format=FORMAT, application=__version__, projectSchema=document.schemaVersion,
                projectId=document.project.projectId, requiredOperators=_operators(document, registry), files=checksums)
            if document.schemaVersion in {"2.6", "2.7", "2.8"}:
                manifest["requiredDeviceTypes"] = _devices(document, deviceTypes)
            archive.writestr("manifest.json", canonicalJson(manifest))
        # Validate the copied bytes, not just the source's earlier hash.
        with tempfile.TemporaryDirectory(prefix="emo-runtime-export-") as directory:
            _extract(temporary, Path(directory), registry, deviceTypes)
        os.replace(temporary, destination)
        return destination
    finally:
        temporary.unlink(missing_ok=True)


def _replaceInputs(original, prepared, schema):
    if schema.get("xWidget") == "file" and schema.get("xFileMode") == "open":
        return prepared
    if isinstance(original, dict) and isinstance(prepared, dict):
        for key, child in schema.get("properties", {}).items():
            if key in prepared:
                value = _replaceInputs(original.get(key), prepared[key], child)
                if key in original or value is not None:
                    original[key] = value
    elif isinstance(original, list) and isinstance(prepared, list):
        for index, item in enumerate(prepared):
            original[index] = _replaceInputs(original[index], item, schema.get("items", {}))
    return original


def _extract(package, stage, registry, deviceTypes=None):
    with zipfile.ZipFile(package) as archive:
        entries = archive.infolist()
        if len(entries) > MAX_ENTRIES or sum(i.file_size for i in entries) > MAX_TOTAL:
            raise ValueError("runtime package file count or size limit")
        names = {}
        for entry in entries:
            name = packagePath(entry.filename)
            key = name.casefold()
            mode = stat.S_IFMT(entry.external_attr >> 16)
            if key in names or entry.is_dir() or mode not in {0, stat.S_IFREG} or entry.flag_bits & 1:
                raise ValueError("duplicate, linked, encrypted or special package entry")
            names[key] = name
        for key in names:
            if any("/".join(key.split("/")[:i]) in names for i in range(1, len(key.split("/")))):
                raise ValueError("package file/directory collision")
        if not {"manifest.json", "project.json"} <= set(names.values()):
            raise ValueError("runtime package metadata missing")
        if any(archive.getinfo(name).file_size > MAX_METADATA for name in ("manifest.json", "project.json")):
            raise ValueError("runtime package metadata limit")
        manifest = readJson(archive.read("manifest.json"))
        if manifest.get("format") != FORMAT or manifest.get("projectSchema") not in {"2.3", "2.4", "2.5", "2.6", "2.7", "2.8"}:
            raise ValueError("unsupported runtime package format")
        files = manifest.get("files")
        if not isinstance(files, dict) or set(files) | {"manifest.json"} != set(names.values()):
            raise ValueError("missing or undeclared package files")
        for name, expected in files.items():
            if name != "project.json" and name.casefold() in RESERVED:
                raise ValueError("reserved package entry")
            target = stage / name
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(name) as source, target.open("xb") as output:
                if _digest(source, output) != expected:
                    raise ValueError("package file digest or size mismatch")
        document = ProjectDocument.model_validate(readJson((stage / "project.json").read_bytes()))
        from emo_master.core.project.dependencies import requireInstalled
        for key, version in manifest.get('requiredOperators', {}).items():
            descriptor = registry.get(key)
            requireInstalled(key, version, descriptor.manifest.version if descriptor else None,
                kind='算子', fieldPath='requiredOperators.'+key, projectId=document.project.projectId)
        for key, lock in manifest.get('requiredDeviceTypes', {}).items():
            declaration = (deviceTypes or {}).get(key)
            requireInstalled(key, lock['version'], declaration.version if declaration else None,
                kind='设备类型', fieldPath='requiredDeviceTypes.'+key, projectId=document.project.projectId)
        if (document.schemaVersion != manifest["projectSchema"] or document.project.projectId != manifest.get("projectId")
                or _operators(document, registry) != manifest.get("requiredOperators")):
            raise ValueError("incompatible operator versions or project identity")
        if document.schemaVersion in {"2.6", "2.7", "2.8"} and _devices(document, deviceTypes) != manifest.get("requiredDeviceTypes"):
            raise ValueError("incompatible device metadata lock")
        _validate(document, stage, registry, deviceTypes, portable=True)
        return document, manifest


def installRuntimePackage(package: Path, destination: Path, *, registry=None, deviceTypes=None,
                          owner=None, projectFile=None, activate=None) -> ProjectDocument:
    """Normal failures restore replaced assets; crashes leave the sibling backup for repair."""
    registry = trustedRegistry() if registry is None else registry
    deviceTypes = trustedDeviceTypes() if deviceTypes is None else deviceTypes
    destination = Path(destination).resolve()
    if owner is not None and (owner.root != destination or owner.stream is None):
        raise ValueError("engineering directory owner mismatch")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with nullcontext() if owner is not None else DirectoryOwner(destination):
        with tempfile.TemporaryDirectory(prefix=".runtime-stage-", dir=destination.parent) as directory:
            stage = Path(directory)
            document, manifest = _extract(package, stage, registry, deviceTypes)
            current = (resolveProjectFile(Path(projectFile) if projectFile else destination)
                       if projectFile or projectFiles(destination) else destination / "project.json")
            if current.parent.resolve() != destination:
                raise ValueError("project file must belong to the destination directory")
            if current.name != "project.json" and current.name in manifest["files"]:
                raise ValueError("package asset overlaps selected project file")
            replaceable = {current.name}
            if current.exists():
                previous = ProjectDocument.model_validate(migrateProjectPayload(loadProjectPayload(current)))
                if previous.project.projectId != document.project.projectId:
                    raise ValueError("choose a new directory for a different project")
                # Evaluate incoming asset targets against the existing output/input contract.
                outputs = []
                def output(raw, mode):
                    path = Path(raw).expanduser()
                    path = (destination / path).resolve() if not path.is_absolute() else path.resolve()
                    if mode == "save":
                        outputs.append(path)
                    elif mode == "open" and path.is_relative_to(destination):
                        replaceable.add(path.relative_to(destination).as_posix())
                    return raw
                if previous.resources is not None:
                    replaceable.update(item.path for item in previous.resources.items.values())
                for flow in previous.workflows.values():
                    for node in flow.nodes:
                        if node.kind == "operator":
                            _files(node.params, registry[node.operatorId].manifest.paramSchema, output)
                _deviceFiles(previous, deviceTypes, output)
                if any((destination / name).resolve() == path or (destination / name).resolve().is_relative_to(path)
                       or path.is_relative_to((destination / name).resolve())
                       for name in manifest["files"] if name != "project.json" for path in outputs):
                    raise ValueError("package asset overlaps existing business output")
            backup = destination.parent / (".runtime-backup-" + uuid4().hex)
            backup.mkdir()
            replaced = []
            try:
                # Publish configuration last. No business data or unrelated files are copied/deleted.
                ordered = [name for name in manifest["files"] if name != "project.json"] + ["project.json"]
                for name in ordered:
                    targetName = current.name if name == "project.json" else name
                    target = _inside(destination, targetName)
                    old = backup / targetName
                    if target.exists():
                        if targetName not in replaceable:
                            raise ValueError("update would overwrite an unrelated file or business output")
                        if not target.is_file() or target.stat().st_nlink > 1:
                            raise ValueError("linked or non-file update target")
                        old.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(target, old)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    _replaceInstalledFile(stage / name, target)
                    replaced.append(targetName)
                if activate is not None:
                    # The local Runtime raises only before durable configuration
                    # commit, so ordinary activation failures restore these files.
                    activate(document)
            except BaseException:
                for name in reversed(replaced):
                    old = backup / name
                    if old.exists():
                        _replaceInstalledFile(old, destination / name)
                    else:
                        (destination / name).unlink(missing_ok=True)
                raise
            # A completed update keeps the old config/assets in one explicit backup, not a version store.
            return document
