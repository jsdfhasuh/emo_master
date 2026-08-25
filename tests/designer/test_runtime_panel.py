from emo_master.apps.designer.ui.runtime_panel import RuntimePanelState


def testRuntimePanelStateUpdatesNodeStatus() -> None:
    state = RuntimePanelState()
    state.applyEvent({"eventType": "node.completed", "nodeId": "n1"})
    assert state.nodeStatus["n1"] == "COMPLETED"


def testRuntimePanelStateExtractsLatestImagePath() -> None:
    state = RuntimePanelState()
    state.applyEvent(
        {
            "eventType": "job.completed",
            "message": "edge image saved: C:/tmp/a.edges.png",
        }
    )
    assert state.latestImagePath == "C:/tmp/a.edges.png"
    assert state.jobStatus == "COMPLETED"


def testRuntimePanelStateExtractsOutputImagePath() -> None:
    state = RuntimePanelState()
    state.applyEvent(
        {
            "eventType": "job.completed",
            "message": "output image saved: C:/tmp/a.out.png",
        }
    )
    assert state.latestImagePath == "C:/tmp/a.out.png"
    assert state.jobStatus == "COMPLETED"


def testRuntimePanelStateUpdateJobStatus() -> None:
    state = RuntimePanelState()
    state.updateJob("RUNNING", "processing")
    assert state.jobStatus == "RUNNING"
    assert state.lastMessage == "processing"


def testRuntimePanelStateKeepsStoppingAsActiveStatus() -> None:
    state = RuntimePanelState()

    state.applyEvent({"eventType": "job.stopping", "message": "stopping"})

    assert state.jobStatus == "STOPPING"
    assert state.lastMessage == "stopping"


def testRuntimePanelStateIgnoresEventsFromAnotherJob() -> None:
    state = RuntimePanelState()
    state.setActiveJob("job-current")
    state.applyEvent(
        {
            "eventType": "node.completed",
            "jobId": "job-current",
            "nodeId": "node",
        }
    )
    state.applyEvent(
        {
            "eventType": "node.failed",
            "jobId": "job-other",
            "nodeId": "node",
        }
    )

    assert state.nodeStatus["node"] == "COMPLETED"


def testRuntimePanelStateRejectsEventsWithoutJobIdWhenJobIsActive() -> None:
    state = RuntimePanelState()
    state.setActiveJob("job-current")

    state.applyEvent(
        {
            "eventType": "node.failed",
            "nodeId": "node",
        }
    )

    assert "node" not in state.nodeStatus


def testRuntimePanelStateClearsNodeStateWhenActiveJobChanges() -> None:
    state = RuntimePanelState()
    state.setActiveJob("job-old")
    state.applyEvent(
        {
            "eventType": "node.completed",
            "jobId": "job-old",
            "nodeId": "node",
        }
    )

    state.setActiveJob("job-new")

    assert state.nodeStatus == {}
    assert state.activeJobId == "job-new"
    assert state.jobStatus == "IDLE"
    assert state.lastMessage == ""


def testRuntimePanelStateRejectsLateEventDuringNewJobStart() -> None:
    state = RuntimePanelState()
    state.setActiveJob("job-old")
    state.setActiveJob(None)

    state.applyEvent(
        {
            "eventType": "node.completed",
            "jobId": "job-old",
            "nodeId": "node",
        }
    )

    assert state.nodeStatus == {}

    state.setActiveJob("job-new")
    state.applyEvent(
        {
            "eventType": "node.started",
            "jobId": "job-new",
            "nodeId": "node",
        }
    )

    assert state.nodeStatus["node"] == "RUNNING"


def testRuntimePanelStateKeepsSameNodeIsolatedAcrossWorkflowRuns() -> None:
    state = RuntimePanelState()
    state.setActiveJob("job-current")

    state.applyEvent(
        {
            "eventType": "node.completed",
            "jobId": "job-current",
            "workflowId": "body",
            "workflowRunId": "run-one",
            "nodeId": "node",
        }
    )
    state.applyEvent(
        {
            "eventType": "node.started",
            "jobId": "job-current",
            "workflowId": "body",
            "workflowRunId": "run-two",
            "nodeId": "node",
        }
    )

    assert state.nodeStatusByWorkflowRun[("body", "run-one", "node")] == "COMPLETED"
    assert state.nodeStatusByWorkflowRun[("body", "run-two", "node")] == "RUNNING"
