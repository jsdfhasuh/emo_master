from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    message: str
    projectId: str = ""
    workflowId: str = ""
    nodeId: str = ""
    fieldPath: str = ""


class WorkflowCompileError(ValueError):
    def __init__(self, issues: list[ValidationIssue]) -> None:
        self.issues = tuple(issues)
        message = "; ".join(issue.message for issue in self.issues)
        super().__init__(message or "workflow compilation failed")


def locatedValidationError(error, code, *, projectId='', workflowId='', nodeId='', fieldPath='', prefix=''):
    """Add the caller's known location while retaining issues from the source."""
    if isinstance(error, WorkflowCompileError):
        issues = error.issues
    elif callable(getattr(error, 'errors', None)):
        issues = [ValidationIssue(code, item['msg'], fieldPath='.'.join(map(str, item['loc'])))
                  for item in error.errors()]
    else:
        issues = [ValidationIssue(code, str(error), fieldPath=fieldPath)]
    return WorkflowCompileError([replace(issue, projectId=issue.projectId or projectId,
        workflowId=issue.workflowId or workflowId, nodeId=issue.nodeId or nodeId,
        fieldPath='.'.join(part for part in (prefix, issue.fieldPath or fieldPath) if part)) for issue in issues])
