"""File parameters whose selected path identifies the production input."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path, PureWindowsPath
import tempfile
from typing import Literal

from emo_master.core.project.models import ProjectDocument, WorkflowDefinition, WorkflowNode
from emo_master.core.project.resources import ParameterTarget, Resource, ResourcePlan, safeRelativePath
from emo_master.core.workflow.errors import ValidationIssue, WorkflowCompileError


NODE_INPUT_FILES = {
    "vision.io.image_loader": "imagePath",
    "vision.io.coordinate_reader": "filePath",
    "vision.io.video_loader": "videoPath",
    "vision.inference.yolo": "modelPath",
    "vision.inference.yolo_classification": "modelPath",
}
_PURPOSE_BY_FIELD: dict[str, Literal["model", "input_image", "input_file"]] = {
    "modelPath": "model", "imagePath": "input_image", "filePath": "input_file", "videoPath": "input_file",
}


def activeNodeInputFile(workflow: WorkflowDefinition, node: WorkflowNode) -> str | None:
    field = NODE_INPUT_FILES.get(node.operatorId or "") if node.kind == "operator" else None
    if field == "videoPath" and any(e.toNode == node.nodeId and e.toPort == field for e in workflow.edges):
        return None
    return field


def nodeInputPathKey(value, projectRoot=None) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    path = Path(value.strip().replace("\\", "/"))
    if path.is_absolute():
        if projectRoot is None or not path.is_relative_to(projectRoot):
            return None
        path = path.relative_to(projectRoot)
    try:
        relative = safeRelativePath(path.as_posix())
    except ValueError:
        return None
    return os.path.normcase(relative).replace("\\", "/")


def nodeInputResource(document, workflow, node, projectRoot=None) -> tuple[str, Resource] | None:
    field = activeNodeInputFile(workflow, node)
    wanted = nodeInputPathKey(node.params.get(field), projectRoot) if field else None
    if wanted is None or document.resources is None:
        return None
    return next(((key, item) for key, item in document.resources.items.items()
                 if nodeInputPathKey(item.path) == wanted), None)


def _inputId(relative: str) -> str:
    # ResourcePlan already requires case-insensitive uniqueness on every OS.
    return "input-" + hashlib.sha256(relative.casefold().encode()).hexdigest()


def _digest(path: Path, output=None) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            size += len(block)
            digest.update(block)
            if output is not None:
                output.write(block)
    return digest.hexdigest(), size


def projectInputFilePath(file, projectRoot, operatorId) -> str:
    """Import an explicitly chosen external file, preserving its original basename."""
    if operatorId not in NODE_INPUT_FILES:
        raise ValueError("该算子没有单文件输入")
    root = Path(projectRoot).resolve(strict=True)
    source = (root / file).resolve(strict=True)
    if not source.is_file():
        raise ValueError(f"请选择文件：{source}")
    if source.is_relative_to(root):
        return source.relative_to(root).as_posix()
    assets = (root / "assets").resolve()
    if not assets.is_relative_to(root):
        raise ValueError("工程资源目录超出工程范围")
    assets.mkdir(exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=assets, delete=False) as stream:
        stage = Path(stream.name)
        try:
            digest, size = _digest(source, stream)
        except BaseException:
            stream.close()
            stage.unlink(missing_ok=True)
            raise
    try:
        directory = (assets / digest).resolve()
        if not directory.is_relative_to(assets):
            raise ValueError("工程资源目录超出工程范围")
        directory.mkdir(exist_ok=True)
        destination = directory / source.name
        if destination.exists():
            if destination.is_symlink() or _digest(destination) != (digest, size):
                raise ValueError(f"同名目标文件内容不同：{destination}")
        else:
            stage.replace(destination)
        return destination.relative_to(root).as_posix()
    finally:
        stage.unlink(missing_ok=True)


def alignNodeInputFiles(document: ProjectDocument, projectRoot, *, only=None, bestEffort=False,
                        refreshExisting=True) -> ProjectDocument:
    """Prepare descriptors for the selected candidate. Never change draft files."""
    result = document.model_copy(deep=True)
    if result.resources is not None:
        alignWorkflowInputFiles(result.workflows, result.resources, result.project.projectId, projectRoot,
                                only=only, bestEffort=bestEffort, refreshExisting=refreshExisting)
    return result


def alignWorkflowInputFiles(workflows: dict[str, WorkflowDefinition], plan: ResourcePlan, projectId: str,
                            projectRoot, *, only=None, bestEffort=False, refreshExisting=True) -> None:
    """Align an owned workflow/package candidate in memory without reading other workflows."""
    root = Path(projectRoot).resolve() if projectRoot else None
    selected = set()
    byPath = {nodeInputPathKey(item.path): key for key, item in plan.items.items()}
    checked = set()
    for wid, workflow in workflows.items():
        for node in workflow.nodes:
            if only is not None and (wid, node.nodeId) not in only:
                continue
            field = NODE_INPUT_FILES.get(node.operatorId or "") if node.kind == "operator" else None
            if not field:
                continue
            selected.add((wid, node.nodeId, field))
            if not activeNodeInputFile(workflow, node):
                continue
            value = node.params.get(field)
            if not isinstance(value, str) or not value.strip():
                continue
            selectedPath = Path(value.strip().replace("\\", "/"))
            pathKey = nodeInputPathKey(value, root)
            try:
                if pathKey is None:
                    if root is None and (selectedPath.is_absolute() or PureWindowsPath(value).is_absolute()):
                        # With no Designer source root this path belongs to the Runtime host.
                        continue
                    raise ValueError(f"输入文件在工程目录外，请在节点中重新选择文件以导入工程：{value}")
                relative = (selectedPath.relative_to(root).as_posix()
                            if root is not None and selectedPath.is_absolute() else selectedPath.as_posix())
                key = byPath.get(pathKey)
                # Keep the spelling declared on the editing host when delivering
                # a project from Windows to a case-sensitive Runtime.
                node.params[field] = plan.items[key].path if key is not None else relative
                if pathKey in checked:
                    continue
                checked.add(pathKey)
                if key is not None and not refreshExisting:
                    continue  # Consumption validates the existing descriptor before using its bytes.
                path = (root / relative).resolve() if root else None
                if root is None or path is None or not path.exists():
                    if key is not None:
                        continue  # Runtime may already have the declared bytes.
                    raise ValueError(f"找不到输入文件：{value}")
                if not path.is_relative_to(root):
                    raise ValueError("输入文件超出工程范围")
                if not path.is_file():
                    raise ValueError(f"输入路径不是文件：{value}")
                digest, size = _digest(path)
                if key is None:
                    key = _inputId(relative)
                    if key in plan.items:
                        raise ValueError(f"工程资源 ID 已用于其他文件：{key}")
                    if any(item.path.casefold() == relative.casefold() for item in plan.items.values()):
                        raise ValueError(f"工程资源中已有仅大小写不同的路径：{relative}")
                    plan.items[key] = Resource(path=relative, sha256=digest, size=size, purpose=_PURPOSE_BY_FIELD[field])
                    byPath[pathKey] = key
                else:
                    item = plan.items[key]
                    plan.items[key] = item.model_copy(update={"sha256": digest, "size": size})
            except (OSError, ValueError) as error:
                if bestEffort:
                    continue
                raise WorkflowCompileError([ValidationIssue("E_NODE_INPUT_FILE", f"{wid}/{node.nodeId}/{field}：{error}",
                    projectId=projectId, workflowId=wid, nodeId=node.nodeId,
                    fieldPath="params." + field)]) from error
    obsolete = set()
    retained = []
    for binding in plan.parameterBindings:
        target = binding.target
        if (isinstance(target, ParameterTarget) and len(target.parameterPath) == 1
                and (target.workflowId, target.nodeId, target.parameterPath[0]) in selected):
            obsolete.add(binding.resourceId)
        else:
            retained.append(binding)
    plan.parameterBindings = retained
    obsolete.update(key for key, item in plan.items.items() if key == _inputId(item.path))
    if obsolete:
        referenced = {binding.resourceId for binding in retained}
        for workflow in workflows.values():
            for node in workflow.nodes:
                field = activeNodeInputFile(workflow, node)
                if field and (key := byPath.get(nodeInputPathKey(node.params.get(field), root))):
                    referenced.add(key)
        for key in obsolete - referenced:
            if key in plan.items and plan.items[key].purpose not in {"page_image", "icon"}:
                del plan.items[key]
