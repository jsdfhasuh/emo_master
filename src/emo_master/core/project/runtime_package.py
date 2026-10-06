"""Portable production projects and offline, output-preserving directory updates."""
from __future__ import annotations

from contextlib import nullcontext
import hashlib
import os
from pathlib import Path
import shutil
import stat
import tempfile
from uuid import uuid4
import zipfile

from emo_master import __version__
from emo_master.core.presentation.validation import validateBindings
from emo_master.core.project.delivery_store import DirectoryOwner
from emo_master.core.project.migration import loadProjectPayload, migrateProjectPayload
from emo_master.core.project.models import ProjectDocument
from emo_master.core.project.runtime_directory import prepareRuntimeProject
from emo_master.core.project.snapshots import _inside, canonicalJson
from emo_master.core.project.test_delivery import packagePath, readJson, trustedRegistry
from emo_master.core.workflow.compiler import WorkflowCompiler


FORMAT = "emo-runtime-project-1"
MAX_TOTAL = 16 * 1024 ** 3
MAX_METADATA = 8 * 1024 ** 2
MAX_ENTRIES = 10000
RESERVED = {"project.json", "manifest.json", ".delivery.lock"}


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


def _validate(document, root, registry):
    prepared = prepareRuntimeProject(document, root, registry)
    WorkflowCompiler(operatorRegistry=registry).compile(prepared)
    issues = validateBindings(prepared, {key: item.manifest for key, item in registry.items()})
    if issues:
        raise ValueError("project page bindings invalid: " + "; ".join(issue.message for issue in issues))
    return prepared


def _operators(document, registry):
    ids = {node.operatorId for flow in document.workflows.values() for node in flow.nodes if node.kind == "operator"}
    return {key: registry[key].manifest.version for key in sorted(ids)}


def buildRuntimePackage(projectDir: Path, outputDir: Path, *, registry=None) -> Path:
    registry = trustedRegistry() if registry is None else registry
    root = Path(projectDir).resolve(strict=True)
    if root.is_file():
        root = root.parent
    outputDir = Path(outputDir).resolve()
    if outputDir.is_relative_to(root):
        raise ValueError("package output must be outside the engineering project")
    document = ProjectDocument.model_validate(migrateProjectPayload(loadProjectPayload(root / "project.json"), enableProduction=True))
    prepared = _validate(document, root, registry)
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
            archive.writestr("manifest.json", canonicalJson(manifest))
        # Validate the copied bytes, not just the source's earlier hash.
        with tempfile.TemporaryDirectory(prefix="emo-runtime-export-") as directory:
            _extract(temporary, Path(directory), registry)
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


def _extract(package, stage, registry):
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
        if manifest.get("format") != FORMAT or manifest.get("projectSchema") != "2.3":
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
        if (document.schemaVersion != manifest["projectSchema"] or document.project.projectId != manifest.get("projectId")
                or _operators(document, registry) != manifest.get("requiredOperators")):
            raise ValueError("incompatible operator versions or project identity")
        _validate(document, stage, registry)
        return document, manifest


def installRuntimePackage(package: Path, destination: Path, *, registry=None, owner=None) -> ProjectDocument:
    """Normal failures restore replaced assets; crashes leave the sibling backup for repair."""
    registry = trustedRegistry() if registry is None else registry
    destination = Path(destination).resolve()
    if owner is not None and (owner.root != destination or owner.stream is None):
        raise ValueError("engineering directory owner mismatch")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with nullcontext() if owner is not None else DirectoryOwner(destination):
        with tempfile.TemporaryDirectory(prefix=".runtime-stage-", dir=destination.parent) as directory:
            stage = Path(directory)
            document, manifest = _extract(package, stage, registry)
            current = destination / "project.json"
            replaceable = {"project.json"}
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
                    target = _inside(destination, name)
                    old = backup / name
                    if target.exists():
                        if name not in replaceable:
                            raise ValueError("update would overwrite an unrelated file or business output")
                        if not target.is_file() or target.stat().st_nlink > 1:
                            raise ValueError("linked or non-file update target")
                        old.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(target, old)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(stage / name, target)
                    replaced.append(name)
            except BaseException:
                for name in reversed(replaced):
                    old = backup / name
                    if old.exists():
                        os.replace(old, destination / name)
                    else:
                        (destination / name).unlink(missing_ok=True)
                raise
            # A completed update keeps the old config/assets in one explicit backup, not a version store.
            return document
