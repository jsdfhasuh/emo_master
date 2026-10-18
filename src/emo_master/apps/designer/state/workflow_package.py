from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
from typing import Collection, Literal, cast
from uuid import uuid4

from pydantic import Field, model_validator, model_serializer
from emo_master.core.project.global_variables import VariableDefinition
from emo_master.core.project.devices import DeviceDefinition, DeviceId
from emo_master.core.project.migration import migrateProjectPayload
from emo_master.apps.designer.state.global_variables import referencedVariables, enableVariables

from emo_master.apps.designer.state.workflow_store import (
    WorkflowState,
    WorkflowStore,
    portTypes,
)
from emo_master.core.project.models import (
    ProjectDocument,
    StrictModel,
    WorkflowDefinition,
)
from emo_master.core.workflow.validation import validateProjectDocument
from emo_master.core.workflow.loop_contracts import loopWorkflowReferenceFields, whileConditionMode


WORKFLOW_PACKAGE_TYPE = "emo-master.workflow"
WORKFLOW_PACKAGE_SCHEMA_VERSION = "1.0"
WORKFLOW_PACKAGE_EXTENSION = ".emowf.json"


class WorkflowPackageDocument(StrictModel):
    schemaVersion: Literal["1.0", "1.1", "1.2"] = "1.0"
    packageType: Literal["emo-master.workflow"] = "emo-master.workflow"
    rootWorkflowId: str
    workflowOrder: list[str]
    workflows: dict[str, WorkflowDefinition]
    requiredOperators: list[str] = Field(default_factory=list)
    globalVariables: dict[str, VariableDefinition] = Field(default_factory=dict)
    devices: dict[DeviceId, DeviceDefinition] = Field(default_factory=dict)

    @model_serializer(mode="wrap")
    def serializeVersion(self, handler):
        payload = handler(self)
        if self.schemaVersion == "1.0":
            payload.pop("globalVariables", None)
        if self.schemaVersion != "1.2":
            payload.pop("devices", None)
        return payload

    @model_validator(mode="after")
    def validateWorkflowIndex(self) -> "WorkflowPackageDocument":
        if self.globalVariables and self.schemaVersion == "1.0":
            raise ValueError("variables require workflow package 1.1")
        if self.devices and self.schemaVersion != "1.2":
            raise ValueError("devices require workflow package 1.2")
        workflowIds = set(self.workflows)
        if not workflowIds:
            raise ValueError("workflow package must contain at least one workflow")
        if self.rootWorkflowId not in workflowIds:
            raise ValueError("rootWorkflowId must reference a packaged workflow")
        if len(self.workflowOrder) != len(set(self.workflowOrder)):
            raise ValueError("workflowOrder must not contain duplicate workflow ids")
        if set(self.workflowOrder) != workflowIds:
            raise ValueError("workflowOrder must contain every workflow exactly once")
        if len(self.requiredOperators) != len(set(self.requiredOperators)):
            raise ValueError("requiredOperators must not contain duplicates")
        return self

    def toPayload(self) -> dict[str, object]:
        return self.model_dump(mode="python")


@dataclass(frozen=True)
class WorkflowPackageExportResult:
    packagePath: Path
    rootWorkflowId: str
    workflowIds: tuple[str, ...]


@dataclass(frozen=True)
class WorkflowPackageImportResult:
    rootWorkflowId: str
    workflowIds: tuple[str, ...]
    workflowIdMap: dict[str, str]
    insertedSubflowNodeId: str | None = None
    parentWorkflowId: str | None = None
    deviceIdMap: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class WorkflowPackageDevicePreview:
    sourceDeviceId: str
    name: str
    deviceTypeId: str
    driverVersion: str
    compatibleTargets: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class WorkflowPackageWorkflowPreview:
    sourceWorkflowId: str
    workflowId: str
    name: str
    isRoot: bool


@dataclass(frozen=True)
class WorkflowPackageIdConflict:
    sourceWorkflowId: str
    workflowId: str


@dataclass(frozen=True)
class WorkflowPackageDependencyPreview:
    sourceWorkflowId: str
    workflowId: str
    targetSourceWorkflowId: str
    targetWorkflowId: str
    relation: str


@dataclass(frozen=True)
class WorkflowPackageExternalPathPreview:
    workflowId: str
    nodeId: str
    paramName: str
    path: str
    fileMode: str
    status: str


@dataclass(frozen=True)
class WorkflowPackageImportPreview:
    sourceRootWorkflowId: str
    rootWorkflowId: str
    workflows: tuple[WorkflowPackageWorkflowPreview, ...]
    workflowIdMap: dict[str, str]
    conflicts: tuple[WorkflowPackageIdConflict, ...]
    dependencies: tuple[WorkflowPackageDependencyPreview, ...]
    requiredOperators: tuple[str, ...]
    missingOperators: tuple[str, ...]
    externalPaths: tuple[WorkflowPackageExternalPathPreview, ...]
    devices: tuple[WorkflowPackageDevicePreview, ...] = ()
    packageFingerprint: str = ""
    targetFingerprint: str = ""


def buildWorkflowPackage(
    store: WorkflowStore, rootWorkflowId: str, *, forInProjectCopy: bool = False,
) -> WorkflowPackageDocument:
    sourceStore = deepcopy(store)
    sourceStore.get(rootWorkflowId)
    sourceStore.ensureBoundaryNodes()
    referencesBySource = _referencesBySource(sourceStore)
    workflowOrder: list[str] = []
    visited: set[str] = set()

    def visit(workflowId: str) -> None:
        if workflowId in visited:
            return
        if workflowId not in sourceStore.workflows:
            raise ValueError(f"workflow dependency does not exist: {workflowId}")
        visited.add(workflowId)
        workflowOrder.append(workflowId)
        for targetWorkflowId in referencesBySource.get(workflowId, []):
            visit(targetWorkflowId)

    visit(rootWorkflowId)
    workflows = {
        workflowId: _definitionFromState(sourceStore.get(workflowId))
        for workflowId in workflowOrder
    }
    referencedDevices = _referencedDeviceIds(workflows)
    resources = sourceStore.projectExtensions.get("resources", {})
    if not forInProjectCopy and any(binding.get("target", {}).get("deviceId") in referencedDevices
           for field in ("parameterBindings", "siteBindings")
           for binding in cast(dict, resources).get(field, [])):
        raise ValueError("设备含资源或现场绑定，请使用完整工程部署包；流程包尚不支持这些依赖")
    catalog = cast(dict, sourceStore.devices.get("definitions", {}))
    if referencedDevices - set(catalog):
        raise ValueError(f"workflow package has missing devices: {sorted(referencedDevices - set(catalog))}")
    document = WorkflowPackageDocument(
        schemaVersion=("1.2" if sourceStore.devices.get("schemaVersion") == "1.0" else
                       "1.1" if "globalVariables" in sourceStore.projectExtensions else "1.0"),
        devices={key: catalog[key] for key in sorted(referencedDevices)},
        globalVariables={key: value for key, value in cast(dict, sourceStore.projectExtensions.get("globalVariables", {})).items()
                         if key in referencedVariables({"workflows": {wid: wf.model_dump() for wid, wf in workflows.items()}})},
        rootWorkflowId=rootWorkflowId,
        workflowOrder=workflowOrder,
        workflows=workflows,
        requiredOperators=_requiredOperatorIds(workflows),
    )
    _validateWorkflowPackage(document)
    return document


def loadWorkflowPackage(filePath: str | Path) -> WorkflowPackageDocument:
    path = Path(filePath)
    parsed = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(parsed, dict):
        raise ValueError("workflow package must contain a JSON object")
    document = WorkflowPackageDocument.model_validate(parsed)
    _validateWorkflowPackage(document)
    return document


def writeWorkflowPackage(
    filePath: str | Path, document: WorkflowPackageDocument
) -> Path:
    _validateWorkflowPackage(document)
    targetPath = normalizeWorkflowPackagePath(filePath)
    targetPath.parent.mkdir(parents=True, exist_ok=True)
    temporaryPath = targetPath.with_name(
        f".{targetPath.name}.{uuid4().hex}.tmp"
    )
    try:
        temporaryPath.write_text(
            json.dumps(document.toPayload(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporaryPath, targetPath)
    finally:
        if temporaryPath.exists():
            temporaryPath.unlink()
    return targetPath


def normalizeWorkflowPackagePath(filePath: str | Path) -> Path:
    path = Path(filePath)
    if path.name.lower().endswith(WORKFLOW_PACKAGE_EXTENSION):
        return path
    if path.suffix.lower() == ".json":
        return path.with_name(f"{path.stem}{WORKFLOW_PACKAGE_EXTENSION}")
    return Path(f"{path}{WORKFLOW_PACKAGE_EXTENSION}")


def previewWorkflowPackageImport(
    store: WorkflowStore,
    document: WorkflowPackageDocument,
    availableOperatorIds: Collection[str] | None = None,
) -> WorkflowPackageImportPreview:
    _validateWorkflowPackage(document)
    _checkVariableConflicts(store, document)
    workingStore = deepcopy(store)
    workflowIdMap = _allocateImportedWorkflowIds(workingStore, document)
    return _buildImportPreview(
        document,
        workflowIdMap,
        availableOperatorIds=availableOperatorIds,
        devices=_devicePreviews(store, document),
        targetFingerprint=_importTargetFingerprint(store),
    )


def importWorkflowPackage(
    store: WorkflowStore,
    document: WorkflowPackageDocument,
    insertIntoWorkflowId: str | None = None,
    *, reuseVariableIds: bool = False,
    deviceMapping: dict[str, str | None] | None = None,
    expectedFingerprint: str | None = None,
    expectedTargetFingerprint: str | None = None,
) -> WorkflowPackageImportResult:
    _validateWorkflowPackage(document)
    if expectedFingerprint is not None and expectedFingerprint != workflowPackageFingerprint(document):
        raise ValueError("流程包在预览后发生变化，请重新预览")
    if expectedTargetFingerprint is not None and expectedTargetFingerprint != _importTargetFingerprint(store):
        raise ValueError("目标工程在预览后发生变化，请重新预览")
    if not reuseVariableIds:
        _checkVariableConflicts(store, document)
    elif any(cast(dict, store.projectExtensions.get("globalVariables", {})).get(key) != value.model_dump()
             for key, value in document.globalVariables.items()):
        raise ValueError("only identical in-project variable definitions may be reused by workflow copy")
    workingStore = deepcopy(store)
    deviceIdMap = _importDevices(workingStore, document, deviceMapping)
    if document.schemaVersion in {"1.1", "1.2"}:
        enableVariables(workingStore)
        cast(dict, workingStore.projectExtensions["globalVariables"]).update(
            {key: value.model_dump() for key, value in document.globalVariables.items()})
    workflowIdMap = _allocateImportedWorkflowIds(workingStore, document)

    for sourceWorkflowId in document.workflowOrder:
        _populateImportedWorkflow(
            workingStore,
            document.workflows[sourceWorkflowId],
            workflowIdMap[sourceWorkflowId],
            workflowIdMap,
            deviceIdMap,
        )

    _mergeRequiredOperators(workingStore, document.requiredOperators)
    mappedRootWorkflowId = workflowIdMap[document.rootWorkflowId]
    insertedSubflowNodeId = None
    if insertIntoWorkflowId is not None:
        insertedSubflowNodeId = _insertImportedSubflowCall(
            workingStore,
            insertIntoWorkflowId,
            mappedRootWorkflowId,
        )
        workingStore.setActiveWorkflow(insertIntoWorkflowId)
    else:
        workingStore.setActiveWorkflow(mappedRootWorkflowId)
    mappedDocument = WorkflowPackageDocument(
        schemaVersion=document.schemaVersion,
        globalVariables=document.globalVariables,
        devices={key: cast(dict, workingStore.devices["definitions"])[key] for key in set(deviceIdMap.values())},
        rootWorkflowId=mappedRootWorkflowId,
        workflowOrder=[workflowIdMap[item] for item in document.workflowOrder],
        workflows={
            workflowIdMap[item]: _definitionFromState(
                workingStore.get(workflowIdMap[item])
            )
            for item in document.workflowOrder
        },
        requiredOperators=list(document.requiredOperators),
    )
    _validateWorkflowPackage(mappedDocument)
    replaceWorkflowStoreState(store, workingStore)
    importedWorkflowIds = tuple(
        workflowIdMap[item] for item in document.workflowOrder
    )
    return WorkflowPackageImportResult(
        rootWorkflowId=mappedRootWorkflowId,
        workflowIds=importedWorkflowIds,
        workflowIdMap=dict(workflowIdMap),
        insertedSubflowNodeId=insertedSubflowNodeId,
        parentWorkflowId=insertIntoWorkflowId,
        deviceIdMap=deviceIdMap,
    )


def _allocateImportedWorkflowIds(
    store: WorkflowStore, document: WorkflowPackageDocument
) -> dict[str, str]:
    workflowIdMap: dict[str, str] = {}
    for sourceWorkflowId in document.workflowOrder:
        source = document.workflows[sourceWorkflowId]
        workflowIdMap[sourceWorkflowId] = store.addWorkflow(
            source.name,
            workflowId=sourceWorkflowId,
            inputs=source.inputs,
            outputs=source.outputs,
        )
    return workflowIdMap


def _buildImportPreview(
    document: WorkflowPackageDocument,
    workflowIdMap: dict[str, str],
    availableOperatorIds: Collection[str] | None,
    devices: tuple[WorkflowPackageDevicePreview, ...] = (),
    targetFingerprint: str = "",
) -> WorkflowPackageImportPreview:
    available = (
        None if availableOperatorIds is None else set(availableOperatorIds)
    )
    workflows = tuple(
        WorkflowPackageWorkflowPreview(
            sourceWorkflowId=sourceWorkflowId,
            workflowId=workflowIdMap[sourceWorkflowId],
            name=document.workflows[sourceWorkflowId].name,
            isRoot=sourceWorkflowId == document.rootWorkflowId,
        )
        for sourceWorkflowId in document.workflowOrder
    )
    conflicts = tuple(
        WorkflowPackageIdConflict(
            sourceWorkflowId=sourceWorkflowId,
            workflowId=workflowIdMap[sourceWorkflowId],
        )
        for sourceWorkflowId in document.workflowOrder
        if sourceWorkflowId != workflowIdMap[sourceWorkflowId]
    )
    requiredOperators = tuple(document.requiredOperators)
    missingOperators = (
        ()
        if available is None
        else tuple(
            operatorId
            for operatorId in requiredOperators
            if operatorId not in available
        )
    )
    return WorkflowPackageImportPreview(
        sourceRootWorkflowId=document.rootWorkflowId,
        rootWorkflowId=workflowIdMap[document.rootWorkflowId],
        workflows=workflows,
        workflowIdMap=dict(workflowIdMap),
        conflicts=conflicts,
        dependencies=_packageDependencyPreviews(document, workflowIdMap),
        requiredOperators=requiredOperators,
        missingOperators=missingOperators,
        externalPaths=_packageExternalPathPreviews(document),
        devices=devices,
        packageFingerprint=workflowPackageFingerprint(document),
        targetFingerprint=targetFingerprint,
    )


def _packageDependencyPreviews(
    document: WorkflowPackageDocument,
    workflowIdMap: dict[str, str],
) -> tuple[WorkflowPackageDependencyPreview, ...]:
    result: list[WorkflowPackageDependencyPreview] = []

    def appendDependency(
        sourceWorkflowId: str,
        targetWorkflowId: object,
        relation: str,
    ) -> None:
        if not isinstance(targetWorkflowId, str):
            return
        mappedTargetWorkflowId = workflowIdMap.get(targetWorkflowId)
        if mappedTargetWorkflowId is None:
            return
        result.append(
            WorkflowPackageDependencyPreview(
                sourceWorkflowId=sourceWorkflowId,
                workflowId=workflowIdMap[sourceWorkflowId],
                targetSourceWorkflowId=targetWorkflowId,
                targetWorkflowId=mappedTargetWorkflowId,
                relation=relation,
            )
        )

    for sourceWorkflowId in document.workflowOrder:
        workflow = document.workflows[sourceWorkflowId]
        for node in workflow.nodes:
            if node.kind == "dispatch":
                for target in cast(list[dict[str, object]], node.params.get("targets", [])):
                    appendDependency(sourceWorkflowId, cast(str, target["workflowId"]), "Dispatch")
                continue
            if node.kind == "subflow":
                appendDependency(
                    sourceWorkflowId, node.targetWorkflowId, "Subflow"
                )
                continue
            if node.kind != "loop":
                continue
            mode = str(node.loop.get("mode", "loop"))
            modeLabel = {
                "repeat": "Repeat",
                "foreach": "ForEach",
                "while": "While",
            }.get(mode, "Loop")
            appendDependency(
                sourceWorkflowId,
                node.loop.get("bodyWorkflowId"),
                f"{modeLabel} · Body",
            )
            if mode == "while" and whileConditionMode(node.loop) == "workflow":
                appendDependency(
                    sourceWorkflowId,
                    node.loop.get("conditionWorkflowId"),
                    "While · Condition",
                )
    return tuple(result)


def _packageExternalPathPreviews(
    document: WorkflowPackageDocument,
) -> tuple[WorkflowPackageExternalPathPreview, ...]:
    result: list[WorkflowPackageExternalPathPreview] = []
    for workflowId in document.workflowOrder:
        workflow = document.workflows[workflowId]
        for node in workflow.nodes:
            properties = node.paramSchema.get("properties", {})
            if not isinstance(properties, dict):
                continue
            for paramName, rawSchema in properties.items():
                if not isinstance(paramName, str) or not isinstance(rawSchema, dict):
                    continue
                if str(rawSchema.get("xWidget", "")).lower() != "file":
                    continue
                rawValue = node.params.get(paramName)
                if not isinstance(rawValue, str) or rawValue.strip() == "":
                    continue
                value = rawValue.strip()
                fileMode = str(rawSchema.get("xFileMode", "open")).lower()
                path = Path(value)
                if fileMode == "save":
                    status = "output"
                elif not path.is_absolute():
                    status = "relative"
                else:
                    try:
                        status = "available" if path.exists() else "missing"
                    except (OSError, ValueError):
                        status = "missing"
                result.append(
                    WorkflowPackageExternalPathPreview(
                        workflowId=workflowId,
                        nodeId=node.nodeId,
                        paramName=paramName,
                        path=value,
                        fileMode=fileMode,
                        status=status,
                    )
                )
    return tuple(result)


def _referencesBySource(store: WorkflowStore) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for reference in store.getWorkflowDependencyReferences():
        sourceWorkflowId = reference.get("sourceWorkflowId")
        targetWorkflowId = reference.get("targetWorkflowId")
        if not isinstance(sourceWorkflowId, str):
            continue
        if not isinstance(targetWorkflowId, str) or targetWorkflowId == "":
            continue
        result.setdefault(sourceWorkflowId, []).append(targetWorkflowId)
    return result


def _definitionFromState(workflow: WorkflowState) -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(
        {
            "name": workflow.name,
            "inputs": deepcopy(workflow.inputs),
            "outputs": deepcopy(workflow.outputs),
            "nodes": deepcopy(workflow.nodes),
            "edges": deepcopy(workflow.edges),
            "layout": deepcopy(workflow.layout),
        }
    )


def _checkVariableConflicts(store, document):
    existing = store.projectExtensions.get("globalVariables", {})
    names = {value["name"] for value in existing.values()}
    for key, value in document.globalVariables.items():
        if key in existing or value.name in names:
            raise ValueError(f"全局变量导入冲突：{value.name} ({key})；请显式重映射 ID / 名称后再导入，不自动合并")


def workflowPackageFingerprint(document: WorkflowPackageDocument) -> str:
    raw = json.dumps(document.toPayload(), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _importTargetFingerprint(store: WorkflowStore) -> str:
    payload = deepcopy(store).toPayload()
    cast(dict, payload["project"]).pop("updatedAt", None)
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _referencedDeviceIds(workflows: dict[str, WorkflowDefinition]) -> set[str]:
    return {binding.deviceId for workflow in workflows.values() for node in workflow.nodes
            for binding in node.deviceBindings.values()}


def _compatibleDevice(source: DeviceDefinition, target: DeviceDefinition) -> bool:
    # Without a loaded driver, equal defaults preserve inherited source semantics.
    return (source.deviceTypeId, source.driverVersion, source.defaults) == (
        target.deviceTypeId, target.driverVersion, target.defaults)


def _devicePreviews(store: WorkflowStore, document: WorkflowPackageDocument
                    ) -> tuple[WorkflowPackageDevicePreview, ...]:
    existing = {key: DeviceDefinition.model_validate(value)
                for key, value in cast(dict, store.devices.get("definitions", {})).items()}
    return tuple(WorkflowPackageDevicePreview(
        sourceDeviceId=key, name=value.name, deviceTypeId=value.deviceTypeId,
        driverVersion=value.driverVersion,
        compatibleTargets=tuple((targetId, target.name) for targetId, target in existing.items()
                                if _compatibleDevice(value, target)),
    ) for key, value in sorted(document.devices.items()))


def _importDevices(store: WorkflowStore, document: WorkflowPackageDocument,
                   mapping: dict[str, str | None] | None) -> dict[str, str]:
    if set(mapping or {}) != set(document.devices) or (document.devices and mapping is None):
        raise ValueError("每个设备依赖必须显式选择复用或新建")
    if document.schemaVersion != "1.2":
        return {}
    upgraded = WorkflowStore(migrateProjectPayload(store.toPayload(), enableDevices=True))
    # Upgrade only project-level fields; preserve the active canvas and allocated IDs.
    store.devices = deepcopy(upgraded.devices)
    store.projectExtensions = deepcopy(upgraded.projectExtensions)
    catalog = cast(dict, store.devices["definitions"])
    result = {}
    for key, value in document.devices.items():
        targetId = (mapping or {})[key]
        if targetId is not None:
            target = catalog.get(targetId)
            if target is None or not _compatibleDevice(value, DeviceDefinition.model_validate(target)):
                raise ValueError(f"设备映射不兼容或已失效：{key} -> {targetId}；类型、驱动版本和默认参数必须一致")
        else:
            targetId = "device-" + uuid4().hex
            while targetId in catalog:
                targetId = "device-" + uuid4().hex
            raw = value.model_dump()
            raw["enabled"] = False
            catalog[targetId] = raw
        result[key] = targetId
    return result


def _validateWorkflowPackage(document: WorkflowPackageDocument) -> None:
    if set(document.devices) != _referencedDeviceIds(document.workflows):
        raise ValueError("devices must exactly match referenced device dependencies")
    expectedOperators = _requiredOperatorIds(document.workflows)
    if document.requiredOperators != expectedOperators:
        raise ValueError(
            "requiredOperators must exactly match operator nodes: "
            + ", ".join(expectedOperators)
        )
    projectDocument = ProjectDocument.model_validate(
        {
            "schemaVersion": {"1.0": "2.1", "1.1": "2.4", "1.2": "2.6"}[document.schemaVersion],
            **({"globalVariables": document.globalVariables, "presentation": {}, "resources": {}, "production": {}}
               if document.schemaVersion != "1.0" else {}),
            **({"communication": {}} if document.schemaVersion == "1.2" else {}),
            "project": {
                "projectId": "workflow-package-validation",
                "name": "Workflow Package",
                "revision": 1,
                "createdAt": "1970-01-01T00:00:00Z",
                "updatedAt": "1970-01-01T00:00:00Z",
            },
            "entryWorkflowId": document.rootWorkflowId,
            "workflowOrder": list(document.workflowOrder),
            "workflows": {
                workflowId: workflow.model_dump(mode="python")
                for workflowId, workflow in document.workflows.items()
            },
            "runtime": {},
            "dependencies": {"operators": list(document.requiredOperators)},
            "devices": ({"schemaVersion": "1.0", "definitions": document.devices}
                        if document.schemaVersion == "1.2" else {}),
        }
    )
    issues = validateProjectDocument(projectDocument)
    missing = referencedVariables(projectDocument.model_dump()) - set(document.globalVariables)
    if missing:
        raise ValueError(f"workflow package has missing global variables: {sorted(missing)}")
    if issues:
        details = "; ".join(f"{issue.code}: {issue.message}" for issue in issues)
        raise ValueError(f"invalid workflow package: {details}")


def _requiredOperatorIds(
    workflows: dict[str, WorkflowDefinition],
) -> list[str]:
    operatorIds = {
        node.operatorId
        for workflow in workflows.values()
        for node in workflow.nodes
        if node.kind == "operator"
        and isinstance(node.operatorId, str)
        and node.operatorId != ""
    }
    return sorted(operatorIds)


def _populateImportedWorkflow(
    store: WorkflowStore,
    source: WorkflowDefinition,
    targetWorkflowId: str,
    workflowIdMap: dict[str, str],
    deviceIdMap: dict[str, str],
) -> None:
    target = store.get(targetWorkflowId)
    target.inputs = deepcopy(source.inputs)
    target.outputs = deepcopy(source.outputs)
    boundaryIds = {
        str(node.get("kind")): str(node.get("nodeId"))
        for node in target.nodes
        if node.get("kind") in {"workflow_input", "workflow_output"}
        and isinstance(node.get("nodeId"), str)
    }
    usedNodeIds = set(boundaryIds.values())
    nodeIdMap: dict[str, str] = {}
    for sourceNode in source.nodes:
        if sourceNode.kind in boundaryIds:
            mappedNodeId = boundaryIds[sourceNode.kind]
        else:
            mappedNodeId = _uniqueNodeId(sourceNode.nodeId, usedNodeIds)
        nodeIdMap[sourceNode.nodeId] = mappedNodeId
        usedNodeIds.add(mappedNodeId)

    transformedNodes: list[dict[str, object]] = []
    for sourceNode in source.nodes:
        node = sourceNode.model_dump(mode="python")
        node["nodeId"] = nodeIdMap[sourceNode.nodeId]
        if sourceNode.deviceBindings:
            node["deviceBindings"] = {slot: {"deviceId": deviceIdMap[binding.deviceId]}
                                      for slot, binding in sourceNode.deviceBindings.items()}
        if sourceNode.operatorId == "vision.io.sqlite_writer":
            from emo_master.core.contracts.sqlite_writer import rebindParams
            node["params"] = rebindParams(sourceNode.params, nodeIdMap)
        if sourceNode.kind == "subflow":
            node["targetWorkflowId"] = _mappedWorkflowId(
                sourceNode.targetWorkflowId,
                workflowIdMap,
                "targetWorkflowId",
            )
        if sourceNode.kind == "dispatch":
            for target in node["params"]["targets"]:
                target["workflowId"] = _mappedWorkflowId(target["workflowId"], workflowIdMap, "Dispatch workflowId")
        if sourceNode.kind == "join":
            node["params"]["dispatchNodeId"] = nodeIdMap[cast(str, sourceNode.params["dispatchNodeId"])]
        if sourceNode.kind == "loop":
            loop = deepcopy(sourceNode.loop)
            for fieldName in loopWorkflowReferenceFields(loop):
                if fieldName not in loop:
                    continue
                loop[fieldName] = _mappedWorkflowId(
                    loop.get(fieldName), workflowIdMap, f"loop.{fieldName}"
                )
            node["loop"] = loop
        transformedNodes.append(node)

    transformedEdges: list[dict[str, object]] = []
    for sourceEdge in source.edges:
        edge = sourceEdge.model_dump(mode="python")
        edge["fromNode"] = nodeIdMap[sourceEdge.fromNode]
        edge["toNode"] = nodeIdMap[sourceEdge.toNode]
        transformedEdges.append(edge)

    transformedPositions: dict[str, dict[str, float]] = {}
    for sourceNodeId, position in source.layout.nodePositions.items():
        mappedPositionNodeId = nodeIdMap.get(sourceNodeId)
        if mappedPositionNodeId is not None:
            transformedPositions[mappedPositionNodeId] = deepcopy(position)

    target.nodes = transformedNodes
    target.edges = transformedEdges
    target.layout = {"nodePositions": transformedPositions}
    store.ensureBoundaryNodes(targetWorkflowId)


def _mappedWorkflowId(
    sourceWorkflowId: object,
    workflowIdMap: dict[str, str],
    fieldName: str,
) -> str:
    if not isinstance(sourceWorkflowId, str) or sourceWorkflowId not in workflowIdMap:
        raise ValueError(f"{fieldName} must reference a packaged workflow")
    return workflowIdMap[sourceWorkflowId]


def _uniqueNodeId(sourceNodeId: str, usedNodeIds: set[str]) -> str:
    if sourceNodeId not in usedNodeIds:
        return sourceNodeId
    base = f"{sourceNodeId}-imported"
    candidate = base
    index = 2
    while candidate in usedNodeIds:
        candidate = f"{base}-{index}"
        index += 1
    return candidate


def _mergeRequiredOperators(store: WorkflowStore, requiredOperators: list[str]) -> None:
    dependencies = deepcopy(store.dependencies)
    rawOperators = dependencies.get("operators", [])
    operators = list(rawOperators) if isinstance(rawOperators, list) else []
    for operatorId in requiredOperators:
        if operatorId not in operators:
            operators.append(operatorId)
    dependencies["operators"] = operators
    store.dependencies = dependencies


def _insertImportedSubflowCall(
    store: WorkflowStore,
    parentWorkflowId: str,
    targetWorkflowId: str,
) -> str:
    if parentWorkflowId == targetWorkflowId:
        raise ValueError("subflow cannot target its current workflow")
    parent = store.get(parentWorkflowId)
    target = store.get(targetWorkflowId)
    usedNodeIds = {
        str(node.get("nodeId"))
        for node in parent.nodes
        if isinstance(node.get("nodeId"), str)
    }
    nodeId = f"node-{uuid4().hex[:8]}"
    while nodeId in usedNodeIds:
        nodeId = f"node-{uuid4().hex[:8]}"
    parent.nodes.append(
        {
            "nodeId": nodeId,
            "operatorId": "",
            "displayName": "Subflow",
            "inputPorts": portTypes(target.inputs),
            "outputPorts": portTypes(target.outputs),
            "paramSchema": {},
            "params": {},
            "kind": "subflow",
            "targetWorkflowId": targetWorkflowId,
            "loop": {},
        }
    )
    rawPositions = parent.layout.get("nodePositions", {})
    positions = deepcopy(rawPositions) if isinstance(rawPositions, dict) else {}
    nodeIndex = len(parent.nodes) - 1
    positions[nodeId] = {
        "x": float(20 + (nodeIndex % 4) * 220),
        "y": float(20 + (nodeIndex // 4) * 120),
    }
    parent.layout = {"nodePositions": positions}
    store.ensureBoundaryNodes(parentWorkflowId)
    return nodeId


def replaceWorkflowStoreState(target: WorkflowStore, source: WorkflowStore) -> None:
    for attributeName in (
        "project",
        "runtime",
        "dependencies",
        "devices",
        "projectExtensions",
        "workflowOrder",
        "entryWorkflowId",
        "activeWorkflowId",
        "workflows",
    ):
        setattr(target, attributeName, deepcopy(getattr(source, attributeName)))
