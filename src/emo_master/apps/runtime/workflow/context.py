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
    callPath: tuple[tuple[str, str], ...] = ()
    cycleId: str = ""
    planRevision: int = 0
    runGroupId: str = ""
    dispatchId: str = ""
    deliveryId: str = ""
    invocationId: str = ""
    targetSlotId: str = ""
    documentRevision: int = 0

    @classmethod
    def root(
        cls,
        jobId: str,
        workflowId: str,
        workspacePath: str = "",
        projectId: str = "",
        cycleId: str = "",
        planRevision: int = 0,
    ) -> "RunContext":
        return cls(
            jobId=jobId,
            workflowId=workflowId,
            workflowRunId=str(uuid4()),
            workspacePath=workspacePath,
            projectId=projectId,
            cycleId=cycleId or str(uuid4()),
            planRevision=planRevision,
        )

    def forNode(self, nodeId: str) -> "RunContext":
        return replace(self, nodeRunId=str(uuid4()), callerNodeId=nodeId)

    def childWorkflow(self, workflowId: str, callerNodeId: str, relation: str = "subflow") -> "RunContext":
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
            callPath=(*self.callPath, (callerNodeId, relation)),
            cycleId=self.cycleId,
            planRevision=self.planRevision,
            runGroupId=self.runGroupId,
            dispatchId=self.dispatchId,
            deliveryId=self.deliveryId,
            invocationId=self.invocationId,
            targetSlotId=self.targetSlotId,
            documentRevision=self.documentRevision,
        )

    def forIteration(self, index: int) -> "RunContext":
        return replace(self, iterationPath=(*self.iterationPath, index))
