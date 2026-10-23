"""Content-addressed, bounded Runtime copies of declared input file parameters.

Committed copies are never overwritten or reclaimed behind an old Job. The cache
is persistent and bounded; exhaustion fails preparation instead of evicting users.
"""
import hashlib
from pathlib import Path
from uuid import uuid4

from emo_master.core.project.runtime_directory import nodeFileSchema

from .store import ConfigurationError


MAX_CACHE_BYTES = 4 * 1024 * 1024 * 1024


def fileDigest(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def retainDeviceInputs(document, previous, deviceTypes, cacheRoot):
    """Compare effective devices using verified immutable paths, not path aliases."""
    root = Path(cacheRoot).resolve()
    def visit(value, old, schema):
        if schema.get("xWidget") == "file" and schema.get("xFileMode") == "open" and value and old:
            retained = Path(old)
            if retained.is_relative_to(root):
                if not retained.is_file() or fileDigest(retained) != retained.stem:
                    raise ConfigurationError("E_CONFIG_RESOURCE_CORRUPT", "retained device input is missing or corrupt")
                if fileDigest(value) == retained.stem:
                    return old
        if isinstance(value, dict):
            for key, child in schema.get("properties", {}).items():
                if key in value:
                    value[key] = visit(value[key], old.get(key) if isinstance(old, dict) else None, child)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                value[index] = visit(item, old[index] if isinstance(old, list) and index < len(old) else None,
                                     schema.get("items", {}))
        return value
    for key, definition in document.devices.definitions.items():
        old = previous.devices.definitions.get(key)
        if old is None or old.deviceTypeId != definition.deviceTypeId:
            continue
        declaration = deviceTypes[definition.deviceTypeId]
        for field in ("connection", "defaults"):
            setattr(definition, field, visit(getattr(definition, field), getattr(old, field),
                                            getattr(declaration, field + "Schema")))
    return document


def prepareResources(document, registry, deviceTypes, cacheRoot, cancelled, *, previous=None, raw=None, previousRaw=None, journal=None):
    result = document.model_copy(deep=True)
    root = Path(cacheRoot).resolve()
    root.mkdir(parents=True, exist_ok=True)
    staging = root / uuid4().hex
    created = []
    expected = {}
    if result.resources:
        for key, path in result._pageResourcePaths.items():
            expected[str(Path(path).resolve())] = result.resources.items[key].sha256
        for binding in result.resources.parameterBindings:
            target = binding.target
            if hasattr(target, "deviceId"):
                value = getattr(result.devices.definitions[target.deviceId], target.section)
            else:
                value = next(n.params for n in result.workflows[target.workflowId].nodes if n.nodeId == target.nodeId)
            for part in target.parameterPath:
                value = value[part]
            expected[str(Path(value).resolve())] = result.resources.items[binding.resourceId].sha256

    def copyInput(path, old=None):
        source = Path(path).resolve(strict=True)
        if source.is_relative_to(root):
            return str(source)
        size = source.stat().st_size
        used = sum(p.stat().st_size for p in root.rglob("*") if p.is_file())
        if used + size > MAX_CACHE_BYTES:
            raise ConfigurationError("E_CONFIG_RESOURCE_CAPACITY", "prepared resource cache capacity exhausted")
        staging.mkdir(exist_ok=True)
        temporary = staging / (uuid4().hex + ".pending")
        if journal is not None:
            journal.reserve(temporary)
        digest = hashlib.sha256()
        try:
            with source.open("rb") as incoming, temporary.open("xb") as outgoing:
                copied = 0
                while chunk := incoming.read(1024 * 1024):
                    if cancelled.is_set():
                        raise ConfigurationError("E_CONFIG_CANCELLED", "resource preparation cancelled")
                    copied += len(chunk)
                    if copied > size:
                        raise ConfigurationError("E_CONFIG_RESOURCE_CHANGED", "input grew during preparation")
                    outgoing.write(chunk)
                    digest.update(chunk)
            if copied != size:
                raise ConfigurationError("E_CONFIG_RESOURCE_CHANGED", "input changed during preparation")
            if str(source) in expected and digest.hexdigest() != expected[str(source)]:
                raise ConfigurationError("E_CONFIG_RESOURCE_CHANGED", "copied input differs from declared hash")
            if old and Path(old).is_file() and fileDigest(old) == digest.hexdigest():
                return old
            target = staging / (digest.hexdigest() + source.suffix.lower())
            if target.exists():
                if fileDigest(target) != digest.hexdigest():
                    raise ConfigurationError("E_CONFIG_RESOURCE_CORRUPT", "prepared resource hash mismatch")
            else:
                if journal is not None:
                    journal.reserve(target)
                temporary.replace(target)
                created.append(target)
            return str(target)
        finally:
            if temporary.exists():
                temporary.unlink()
            if journal is not None:
                journal.forget(temporary)
            if staging.exists() and not any(staging.iterdir()):
                staging.rmdir()

    def visit(value, schema, old=None, source=None, oldSource=None):
        if schema.get("xWidget") == "file" and schema.get("xFileMode") == "open" and value:
            if source == oldSource and old and Path(old).is_relative_to(root):
                if not Path(old).is_file() or fileDigest(old) != Path(old).stem:
                    raise ConfigurationError("E_CONFIG_RESOURCE_CORRUPT", "retained input copy is missing or corrupt")
                return old
            return copyInput(value, old)
        if isinstance(value, dict):
            for key, child in schema.get("properties", {}).items():
                if key in value:
                    value[key] = visit(value[key], child, (old or {}).get(key),
                                       (source or {}).get(key), (oldSource or {}).get(key))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                value[index] = visit(item, schema.get("items", {}))
        return value

    def nodeAt(doc, wid, nid):
        if doc is None or wid not in doc.workflows:
            return None
        return next((n for n in doc.workflows[wid].nodes if n.nodeId == nid), None)

    try:
        reuse = previousRaw is not None and raw is not None and raw.resources == previousRaw.resources
        for key, path in result._pageResourcePaths.items():
            old = previous._pageResourcePaths.get(key) if previous is not None else None
            result._pageResourcePaths[key] = copyInput(path, old)
        for wid, workflow in result.workflows.items():
            for node in workflow.nodes:
                old = nodeAt(previous, wid, node.nodeId)
                source, oldSource = nodeAt(raw, wid, node.nodeId), nodeAt(previousRaw, wid, node.nodeId)
                if node.operatorId in registry:
                    node.params = visit(node.params, nodeFileSchema(node, workflow, registry[node.operatorId].manifest.paramSchema),
                        old.params if old and reuse else None, source.params if source else None,
                        oldSource.params if oldSource else None)
                for slot, binding in node.deviceBindings.items():
                    declaration = deviceTypes[result.devices.definitions[binding.deviceId].deviceTypeId]
                    if slot in node.deviceOverrides:
                        node.deviceOverrides[slot] = visit(node.deviceOverrides[slot], declaration.defaultsSchema)
        for key, definition in result.devices.definitions.items():
            declaration = deviceTypes[definition.deviceTypeId]
            for field, schema in (("connection", declaration.connectionSchema), ("defaults", declaration.defaultsSchema)):
                old = previous.devices.definitions.get(key) if previous is not None else None
                source = raw.devices.definitions.get(key) if raw is not None else None
                oldSource = previousRaw.devices.definitions.get(key) if previousRaw is not None else None
                setattr(definition, field, visit(getattr(definition, field), schema,
                    getattr(old, field) if old and reuse else None, getattr(source, field) if source else None,
                    getattr(oldSource, field) if oldSource else None))
        if journal is not None:
            journal.expose(created)
        return result, created
    except BaseException:
        discardResources(created, root, journal=journal)
        raise


def discardResources(paths, root, *, journal=None):
    root = Path(root).resolve()
    for path in paths:
        target = Path(path).resolve()
        if target.parent.parent != root:
            raise ConfigurationError("E_CONFIG_RESOURCE_PATH", "refusing cleanup outside prepared cache")
        target.unlink(missing_ok=True)
        if journal is not None:
            journal.forget(target)
        if target.parent.exists() and not any(target.parent.iterdir()):
            target.parent.rmdir()
