from __future__ import annotations

from dataclasses import dataclass, replace
from uuid import uuid4


@dataclass(frozen=True)
class RunContext:
    jobId: str
    workflowId: str
    workflowRunId: str
    parentWorkflowRunId: str = ""
    callerNodeId: str = ""
    nodeRunId: str = ""
    callDepth: int = 0
    iterationPath: tuple[int, ...] = ()
    workspacePath: str = ""
    projectId: str = ""

    @classmethod
    def root(
        cls,
        jobId: str,
        workflowId: str,
        workspacePath: str = "",
        projectId: str = "",
    ) -> "RunContext":
        return cls(
            jobId=jobId,
            workflowId=workflowId,
            workflowRunId=str(uuid4()),
            workspacePath=workspacePath,
            projectId=projectId,
        )

    def forNode(self, nodeId: str) -> "RunContext":
        return replace(self, nodeRunId=str(uuid4()), callerNodeId=nodeId)

    def childWorkflow(self, workflowId: str, callerNodeId: str) -> "RunContext":
        return RunContext(
            jobId=self.jobId,
            workflowId=workflowId,
            workflowRunId=str(uuid4()),
            parentWorkflowRunId=self.workflowRunId,
            callerNodeId=callerNodeId,
            callDepth=self.callDepth + 1,
            iterationPath=self.iterationPath,
            workspacePath=self.workspacePath,
            projectId=self.projectId,
        )

    def forIteration(self, index: int) -> "RunContext":
        return replace(self, iterationPath=(*self.iterationPath, index))
