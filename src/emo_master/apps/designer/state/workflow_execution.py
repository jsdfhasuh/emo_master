"""Latest workflow invocation per Job, independent of the selected inspector."""
from dataclasses import dataclass, field


@dataclass
class WorkflowExecutionState:
    # One record per workflow, not one per While iteration.
    workflows: dict[str, dict] = field(default_factory=dict)
    sequence: int = 0

    def clear(self):
        self.workflows.clear()
        self.sequence = 0

    def applyEvent(self, event):
        kind = event.get('eventType', '')
        if kind not in {'workflow.started', 'workflow.completed', 'workflow.failed',
                        'node.started', 'node.completed', 'node.failed', 'node.skipped'}:
            return
        workflow = event.get('workflowId')
        run = event.get('workflowRunId', '')
        if not isinstance(workflow, str) or not workflow or not isinstance(run, str):
            return
        sequence = event.get('sequence', 0)
        sequence = sequence if type(sequence) is int and sequence > 0 else 0
        if sequence and sequence <= self.sequence:
            return
        self.sequence = max(self.sequence, sequence)
        previous = self.workflows.get(workflow)
        if previous is None or (run and run != previous['runId']):
            # A delayed completion from a previous invocation cannot replace
            # a newer live invocation, even when a legacy event has no sequence.
            if previous and kind not in {'workflow.started', 'node.started'}:
                return
            previous = dict(runId=run, parentRunId=event.get('parentWorkflowRunId', ''),
                            status='RUNNING', nodeId='')
            self.workflows[workflow] = previous
        payload = event.get('payload')
        payload = payload if isinstance(payload, dict) else {}
        cancelled = (event.get('code') or payload.get('code')) == 'E_CANCELLED'
        if kind == 'workflow.started':
            previous.update(status='RUNNING', nodeId='')
        elif kind == 'workflow.completed':
            previous.update(status='COMPLETED', nodeId='')
        elif kind in {'workflow.failed', 'node.failed'}:
            previous.update(status='ABORTED' if cancelled else 'FAILED')
            if kind == 'node.failed':
                previous['nodeId'] = event.get('nodeId', '')
        elif kind == 'node.started':
            previous.update(status='RUNNING', nodeId=event.get('nodeId', ''))
        elif event.get('nodeId') == previous['nodeId']:
            previous['nodeId'] = ''

    def statuses(self, jobStatus):
        parents = {item['parentRunId'] for item in self.workflows.values()
                   if item['status'] == 'RUNNING' and item['parentRunId']}
        result = {}
        for workflow, item in self.workflows.items():
            status = item['status']
            if status == 'RUNNING':
                if jobStatus in {'COMPLETED', 'FAILED', 'ABORTED', 'REJECTED', 'STOPPING', 'START_UNCERTAIN'}:
                    status = jobStatus
                elif item['runId'] in parents:
                    status = 'WAITING_CHILD'
            result[workflow] = status
        return result
