"""Bounded portable dependencies; import never overwrites an existing file."""
import base64
from copy import deepcopy
import hashlib
from pathlib import Path
import tempfile
from uuid import uuid4

from emo_master.core.project.resources import ResourcePlan, DeviceParameterTarget
from emo_master.core.project.snapshots import _setPath


MAX_RESOURCE_BYTES = 64 * 1024 * 1024
MAX_PACKAGE_BYTES = 96 * 1024 * 1024


def dependencyPlan(store, workflows, devices):
    raw = deepcopy(store.projectExtensions.get("resources", {}))
    for field in ("parameterBindings", "siteBindings"):
        raw[field] = [binding for binding in raw.get(field, [])
            if binding["target"].get("deviceId") in devices
            or binding["target"].get("workflowId") in workflows]
    used = {binding["resourceId"] for binding in raw["parameterBindings"]}
    raw["items"] = {key: value for key, value in raw.get("items", {}).items() if key in used}
    if used - set(raw["items"]):
        raise ValueError("workflow package resource dependency is missing")
    return ResourcePlan.model_validate(raw)


def targetParameters(document, target):
    if isinstance(target, DeviceParameterTarget):
        return getattr(document.devices[target.deviceId], target.section)
    node = next((node for node in document.workflows[target.workflowId].nodes if node.nodeId == target.nodeId), None)
    if node is None or node.kind != "operator":
        raise ValueError("workflow package binding target is missing or not an operator")
    return node.params


def validateDependencies(document, *, referencesOnly=False):
    plan = ResourcePlan.model_validate(document.resources.model_dump(mode="json"))
    used = {binding.resourceId for binding in plan.parameterBindings}
    if used != set(plan.items):
        raise ValueError("workflow package resources must exactly match referenced dependencies")
    for binding in [*plan.parameterBindings, *plan.siteBindings]:
        targetParameters(document, binding.target)
    if referencesOnly:
        return
    total = sum(item.size for item in plan.items.values())
    if total > MAX_RESOURCE_BYTES:
        raise ValueError("workflow package resources exceed the 64 MiB portable budget")
    for binding in plan.siteBindings:
        value = targetParameters(document, binding.target)
        for part in binding.target.parameterPath:
            value = value.get(part) if isinstance(value, dict) else None
        if value != "":
            raise ValueError("portable site binding values must be empty")
    if set(document.resourceData) != set(plan.items):
        raise ValueError("workflow package must embed every declared resource")
    for key, item in plan.items.items():
        encoded = document.resourceData[key]
        if len(encoded) != 4 * ((item.size + 2) // 3):
            raise ValueError("workflow package resource encoded size mismatch")
        data = base64.b64decode(encoded, validate=True)
        if len(data) != item.size or hashlib.sha256(data).hexdigest() != item.sha256:
            raise ValueError("workflow package resource size or digest mismatch")


def embedDependencies(document, sourceRoot):
    if document.resources.items and sourceRoot is None:
        raise ValueError("save the source project before exporting resource dependencies")
    if sum(item.size for item in document.resources.items.values()) > MAX_RESOURCE_BYTES:
        raise ValueError("workflow package resources exceed the 64 MiB portable budget")
    if document.resources.items:
        root = Path(sourceRoot).resolve(strict=True)
        for key, item in document.resources.items.items():
            source = (root / item.path).resolve(strict=True)
            if not source.is_relative_to(root) or not source.is_file() or source.stat().st_size != item.size:
                raise ValueError("workflow package resource is outside the project or has changed size")
            with source.open("rb") as stream:
                data = stream.read(item.size + 1)
            if len(data) != item.size or hashlib.sha256(data).hexdigest() != item.sha256:
                raise ValueError("workflow package resource size or digest mismatch")
            document.resourceData[key] = base64.b64encode(data).decode("ascii")
    for binding in document.resources.parameterBindings:
        _setPath(targetParameters(document, binding.target), binding.target.parameterPath,
                 document.resources.items[binding.resourceId].path)
    # Site-specific values are not portable, including addresses and secret refs.
    for binding in document.resources.siteBindings:
        _setPath(targetParameters(document, binding.target), binding.target.parameterPath, "")


def deviceDependencies(document):
    return {binding.target.deviceId for binding in
        [*document.resources.parameterBindings, *document.resources.siteBindings]
        if isinstance(binding.target, DeviceParameterTarget)}


def remapDependencies(store, document, workflowIds, nodeIds, deviceIds, *, inProjectCopy=False):
    plan = ResourcePlan.model_validate(store.projectExtensions.get("resources", {}))
    source = document.resources
    resourceIds = {}
    siteIds = {}
    for key, value in source.items.items():
        if inProjectCopy:
            if plan.items.get(key) != value:
                raise ValueError("in-project resource dependency changed")
            resourceIds[key] = key
        else:
            targetId = "resource-" + uuid4().hex
            resourceIds[key] = targetId
            plan.items[targetId] = value.model_copy(deep=True)
            plan.items[targetId].path = "workflow-resources-pending/" + targetId + Path(value.path).suffix
    for field in ("parameterBindings", "siteBindings"):
        for binding in getattr(source, field):
            updated = binding.model_copy(deep=True)
            target = updated.target
            if isinstance(target, DeviceParameterTarget):
                if inProjectCopy:
                    if binding not in getattr(plan, field) or deviceIds[target.deviceId] != target.deviceId:
                        raise ValueError("in-project device dependency changed")
                    continue
                target.deviceId = deviceIds[target.deviceId]
            else:
                originalWorkflowId = target.workflowId
                target.workflowId = workflowIds[originalWorkflowId]
                target.nodeId = nodeIds[originalWorkflowId][target.nodeId]
            if field == "parameterBindings":
                updated.resourceId = resourceIds[binding.resourceId]
            elif not inProjectCopy:
                updated.field = siteIds.setdefault(binding.field, "site-" + uuid4().hex)
            getattr(plan, field).append(updated)
    return plan, resourceIds


def applyImportedPaths(store, plan, resourceIds):
    for binding in plan.parameterBindings:
        if binding.resourceId not in set(resourceIds.values()):
            continue
        target = binding.target
        if isinstance(target, DeviceParameterTarget):
            params = store.devices["definitions"][target.deviceId][target.section]
        else:
            params = next(node["params"] for node in store.get(target.workflowId).nodes if node["nodeId"] == target.nodeId)
        _setPath(params, target.parameterPath, plan.items[binding.resourceId].path)


def installDependencies(root, document, plan, resourceIds):
    """Stage into a fresh owned directory. Caller rolls back on draft failure."""
    if not resourceIds:
        return None
    if root is None:
        raise ValueError("save the destination project before importing resource dependencies")
    root = Path(root).resolve(strict=True)
    directory = Path(tempfile.mkdtemp(prefix="workflow-resources-", dir=root))
    try:
        for key, targetId in resourceIds.items():
            item = plan.items[targetId]
            path = directory / (targetId + Path(item.path).suffix)
            data = base64.b64decode(document.resourceData[key], validate=True)
            with path.open("xb") as stream:
                stream.write(data)
            item.path = path.relative_to(root).as_posix()
        return directory
    except BaseException:
        discardInstalled(directory)
        raise


def discardInstalled(directory):
    if directory is not None:
        # Only a newly allocated directory, never a user-selected tree.
        for path in directory.iterdir():
            path.unlink()
        directory.rmdir()
