import pytest

from emo_master.apps.designer.services.runtime_client import RuntimeClient
from emo_master.apps.designer.state.project_store import saveProject
from emo_master.apps.designer.state.workflow_store import WorkflowStore
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from tests.designer.qt_wait import waitUntil
from tests.runtime.test_project_source_relocation import loadOnline


@pytest.mark.parametrize('mode', ['continuous', 'single'])
def testRunButtonUsesPublishedProjectMode(ownedDesignerWindow, tmp_path, mode):
    project = tmp_path / 'project' / 'sample.emoproj'
    payload = WorkflowStore().toPayload()
    payload['production'].update(mode=mode, cycleIntervalMs=100, autoStart=True)
    saveProject(project, payload)

    runtime = RuntimeService(dbPath=tmp_path / 'runtime/runtime.db')
    client = RuntimeClient(runtime, ownedRuntimeService=runtime)
    try:
        with ownedDesignerWindow as windows:
            window = windows(client)
            assert window.loadProjectSelection(str(project))
            panel = window.floatingRunPanel
            tooltip = panel.startButton.toolTip()
            if mode == 'continuous':
                assert panel.startButton.text() == '持续运行'
                assert '持续运行' in tooltip and '100 ms' in tooltip
            else:
                assert panel.startButton.text() == '单次运行'
                assert '单次运行' in tooltip
            window.pageCoordinator.sync()
            loadOnline(client, runtime, project, window.pageCoordinator.session.document())
            assert not runtime.jobRepository.all()
            assert panel.startButton.isEnabled()
            panel.startButton.click()
            waitUntil(lambda: window.currentJobId or window.runtimePanelState.jobStatus == 'FAILED', timeout=15)
            job = window.currentJobId
            assert job, window.runtimePanelState.jobMessage

            def completedCycles():
                return {event.workflowRunId for event in runtime.eventStore.read(job)
                        if event.eventType == 'workflow.completed'}

            if mode == 'continuous':
                waitUntil(lambda: len(completedCycles()) >= 3 or runtime.jobRepository.get(job).isTerminal,
                          timeout=15)
                assert len(completedCycles()) >= 3
                assert runtime.jobRepository.get(job).status == 'RUNNING'
                assert panel.stopButton.isEnabled()
                panel.stopButton.click()
            waitUntil(lambda: not window.isJobRunning, timeout=15)
            assert len(runtime.jobRepository.all()) == 1
            record = runtime.jobRepository.get(job)
            if mode == 'continuous':
                assert record.status == 'ABORTED' and record.errorCode == 'E_CANCELLED'
            else:
                assert record.status == 'COMPLETED' and len(completedCycles()) == 1
    finally:
        client.close()
