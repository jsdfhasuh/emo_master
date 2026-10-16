from emo_master.apps.designer.ui.runtime_panel import RuntimePanelState
from emo_master.ui.workflow_labels import aggregateWorkflowStatus


def event(kind, workflow='main', run='run-main', sequence=1, parent='', **values):
    return dict(eventType=kind, workflowId=workflow, workflowRunId=run, sequence=sequence,
                parentWorkflowRunId=parent, jobId='job-1', **values)


def testNestedWorkflowShowsExecutingLeafAndCallingParent():
    state = RuntimePanelState()
    state.setActiveJob('job-1')
    state.updateJob('RUNNING')
    state.applyEvent(event('workflow.started'))
    state.applyEvent(event('node.started', sequence=2, nodeId='call'))
    state.applyEvent(event('workflow.started', 'body', 'run-body', 3, 'run-main'))
    assert state.workflowExecution.statuses(state.jobStatus) == {'main': 'WAITING_CHILD', 'body': 'RUNNING'}
    state.applyEvent(event('workflow.completed', 'body', 'run-body', 4, 'run-main'))
    assert state.workflowExecution.statuses(state.jobStatus) == {'main': 'RUNNING', 'body': 'COMPLETED'}
    state.applyEvent(event('workflow.completed', sequence=5))
    assert state.workflowExecution.statuses(state.jobStatus)['main'] == 'COMPLETED'


def testWhileIterationsKeepOnlyLatestWorkflowAndRejectOldCompletions():
    state = RuntimePanelState()
    state.setActiveJob('job-1')
    for iteration in range(1200):
        state.applyEvent(event('workflow.started', 'body', f'run-{iteration}', iteration * 2 + 1))
        state.applyEvent(event('workflow.completed', 'body', f'run-{iteration}', iteration * 2 + 2))
    assert len(state.workflowExecution.workflows) == 1
    state.applyEvent(event('workflow.started', 'body', 'run-new', 2401))
    state.applyEvent(event('workflow.completed', 'body', 'run-1199', 2400))
    state.applyEvent(event('workflow.failed', 'body', 'run-1199', 0))
    assert state.workflowExecution.statuses('RUNNING') == {'body': 'RUNNING'}
    state.setActiveJob('job-new')
    state.applyEvent(event('workflow.started', sequence=2402))
    assert not state.workflowExecution.workflows
    state.applyEvent(dict(event('workflow.started'), jobId='job-new'))
    assert state.workflowExecution.statuses('RUNNING') == {'main': 'RUNNING'}


def testTerminalStatusFallbackStopsLiveBadgesEvenWithoutWorkflowTerminalEvent():
    state = RuntimePanelState()
    state.setActiveJob('job-1')
    state.applyEvent(event('workflow.started'))
    state.applyEvent(event('workflow.started', 'done', 'run-done', 2, 'run-main'))
    state.applyEvent(event('workflow.completed', 'done', 'run-done', 3, 'run-main'))
    for status in ('STOPPING', 'ABORTED', 'FAILED', 'COMPLETED', 'START_UNCERTAIN'):
        state.updateJob(status)
        assert state.workflowExecution.statuses(state.jobStatus) == {'main': status, 'done': 'COMPLETED'}


def testCancellationIsStoppedNotRedAndTechnicalFailureRemainsRed():
    state = RuntimePanelState()
    state.applyEvent(event('workflow.failed', code='E_CANCELLED'))
    assert state.workflowExecution.statuses('ABORTED')['main'] == 'ABORTED'
    state.applyEvent(event('workflow.started', run='new', sequence=2))
    state.applyEvent(event('node.failed', run='new', sequence=3, nodeId='camera', code='E_CAMERA'))
    assert state.workflowExecution.statuses('RUNNING')['main'] == 'FAILED'
    assert state.workflowExecution.workflows['main']['status'] == 'FAILED'
    assert aggregateWorkflowStatus(['COMPLETED', 'RUNNING']) == 'RUNNING'
    assert aggregateWorkflowStatus(['RUNNING', 'FAILED']) == 'FAILED'


def testDifferentJobsHaveIndependentStatesForSameSubworkflow():
    first, second = RuntimePanelState(), RuntimePanelState()
    first.setActiveJob('job-1')
    second.setActiveJob('job-2')
    first.applyEvent(event('workflow.started', 'shared'))
    second.applyEvent(dict(event('workflow.completed', 'shared'), jobId='job-2'))
    assert first.workflowExecution.statuses('RUNNING') == {'shared': 'RUNNING'}
    assert second.workflowExecution.statuses('RUNNING') == {'shared': 'COMPLETED'}
