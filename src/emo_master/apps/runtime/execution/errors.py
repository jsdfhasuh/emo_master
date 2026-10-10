from __future__ import annotations

from typing import Mapping


class WorkflowExecutionError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        nodeId: str = "",
        metrics: Mapping[str, object] | None = None,
        diagnostics: Mapping[str, object] | None = None,
    ) -> None:
        self.code = code
        self.nodeId = nodeId
        self.metrics = dict(metrics or {})
        self.diagnostics = dict(diagnostics or {})
        super().__init__(message)
