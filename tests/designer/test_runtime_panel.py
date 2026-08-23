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
