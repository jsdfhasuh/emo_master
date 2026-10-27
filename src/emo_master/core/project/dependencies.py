"""Installed-operator requirements; online writes never install executable code."""
from emo_master.core.workflow.errors import ValidationIssue, WorkflowCompileError


def requireInstalled(key, version, installed, *, kind, fieldPath, projectId=''):
    if installed is None or version is not None and installed != version:
        raise WorkflowCompileError([ValidationIssue('E_OPERATOR_DEPENDENCY' if kind == '算子' else 'E_DEVICE_DEPENDENCY',
            f'{kind} {key}；要求 {version or "已安装版本"}；当前安装 {installed or "未安装"}',
            projectId=projectId, fieldPath=fieldPath)])


def validateOperatorDependencies(document, registry):
    seen = set()
    for item in document.dependencies.operators:
        if isinstance(item, str):
            key, version = item, None
        elif isinstance(item, dict) and set(item) == {"operatorId", "version"}:
            key, version = item["operatorId"], item["version"]
            if not isinstance(version, str) or not version:
                raise ValueError("operator dependency version must be a non-empty exact version")
        else:
            raise ValueError("operator dependency must be an ID or an operatorId/version lock")
        descriptor = registry.get(key) if isinstance(key, str) else None
        requireInstalled(key, version, descriptor.manifest.version if descriptor else None, kind='算子',
            projectId=document.project.projectId, fieldPath='dependencies.operators.'+str(key))
        if key in seen:
            raise ValueError("duplicate operator dependency: " + key)
        seen.add(key)
