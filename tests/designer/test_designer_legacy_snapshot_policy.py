"""Offline policy/inspector regressions; no device or real-project acceptance."""
from types import SimpleNamespace

import pytest

from emo_master.apps.designer.services.runtime_client import (
    PreviewSource,
    PreviewSourceListing,
    RuntimeClient,
    RuntimeClientError,
)
from emo_master.apps.designer.services.runtime_worker import RuntimeWorker, _runWorker
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb


_CAPABILITIES = ["legacy_snapshot_policy_v1", "preview_snapshot_origin_v1", "start_request_lookup"]


def _externalClient(capabilities=None):
    requests = []

    def start(request, context):
        requests.append(request)
        return pb.StartJobReply(ok=True, job_id="new-job", runtime_instance_id="instance",
                                start_request_id=request.start_request_id,
                                legacy_snapshot_policy=request.legacy_snapshot_policy)

    display = None
    if capabilities is not None:
        display = SimpleNamespace(Capabilities=lambda request, timeout: pb.DisplayCapabilities(
            runtime_instance_id="instance", capabilities=capabilities))
    return RuntimeClient(SimpleNamespace(StartJob=start), displayService=display), requests


@pytest.mark.parametrize("capabilities", [None, [], ["start_request_lookup"],
    ["legacy_snapshot_policy_v1", "start_request_lookup"],
    ["legacy_snapshot_policy_v1", "preview_snapshot_origin_v1"]])
def testUnsupportedExternalNoneNeverCallsStart(capabilities):
    client, requests = _externalClient(capabilities)
    with pytest.raises(RuntimeClientError):
        client.startJob("project", legacySnapshotPolicy="NONE", startRequestId="request",
                        expectedRuntimeInstanceId="instance")
    assert requests == []
    assert client._presentationService is None
    assert client._presentationServer is None


@pytest.mark.parametrize("policy", ["", "ALL"])
def testOldExternalAllKeepsOriginalNoPagePath(policy):
    client, requests = _externalClient()
    assert client.prepareStart(False, legacySnapshotPolicy=policy) == ""
    client.startJob("project", legacySnapshotPolicy=policy)
    assert requests[0].legacy_snapshot_policy == "ALL"
    assert not requests[0].capture_presentation
    assert not requests[0].start_request_id
    assert client._presentationService is None


def testExternalNoneNegotiatesAndCarriesRequestIdentity():
    client, requests = _externalClient(_CAPABILITIES)
    client.startJob("project", legacySnapshotPolicy="NONE", startRequestId="request",
                    expectedRuntimeInstanceId="instance")
    assert len(requests) == 1
    assert requests[0].legacy_snapshot_policy == "NONE"
    assert requests[0].start_request_id == "request"
    assert requests[0].expected_runtime_instance_id == "instance"
    assert not requests[0].capture_presentation


@pytest.mark.parametrize("requestId,generation", [("", ""), ("request", ""), ("", "instance")])
def testDirectNoneRequiresRetainedIdentityBeforePreparation(requestId, generation):
    client, requests = _externalClient(_CAPABILITIES)

    def unexpectedPreparation(*args, **kwargs):
        raise AssertionError("identity must be validated before preparing resources")

    client.prepareStart = unexpectedPreparation
    with pytest.raises(RuntimeClientError) as error:
        client.startJob("project", legacySnapshotPolicy="NONE", startRequestId=requestId,
                        expectedRuntimeInstanceId=generation)
    assert error.value.code == "E_START_IDENTITY_REQUIRED"
    assert not requests


def testDirectNoneRejectsChangedGenerationBeforeStart():
    client, requests = _externalClient(_CAPABILITIES)
    with pytest.raises(RuntimeClientError) as error:
        client.startJob("project", legacySnapshotPolicy="NONE", startRequestId="request",
                        expectedRuntimeInstanceId="old-instance")
    assert error.value.code == "E_RUNTIME_IDENTITY"
    assert not requests


def testDirectNoneLostReplyIsReconciledWithCallerIdentityWithoutAnotherStart():
    client, requests = _externalClient(_CAPABILITIES)
    start = client.runtimeService.StartJob
    savedReplies, queries = [], []

    def lostReply(request, context):
        savedReplies.append(start(request, context))
        raise TimeoutError("acknowledgement lost after acceptance")

    def lookup(request, context):
        queries.append((request.start_request_id, request.runtime_instance_id))
        return savedReplies[0]

    client.runtimeService.StartJob = lostReply
    client.runtimeService.GetStartRequest = lookup
    with pytest.raises(TimeoutError):
        client.startJob("project", legacySnapshotPolicy="NONE", startRequestId="retained-token",
                        expectedRuntimeInstanceId="instance")
    reply = client.getStartRequest("retained-token", "instance")
    assert len(requests) == 1
    assert requests[0].start_request_id == reply.start_request_id == "retained-token"
    assert requests[0].expected_runtime_instance_id == reply.runtime_instance_id == "instance"
    assert reply.job_id == "new-job" and reply.legacy_snapshot_policy == "NONE"
    assert queries == [("retained-token", "instance")]


def testEmbeddedNoneDoesNotProvisionPresentation(tmp_path):
    from emo_master.apps.runtime.grpc_server.service import RuntimeService
    runtime = RuntimeService(dbPath=tmp_path / "runtime.sqlite3", workspaceRoot=tmp_path / "jobs")
    client = RuntimeClient(runtime, ownedRuntimeService=runtime)
    try:
        assert runtime.supportsLegacySnapshotPolicy
        assert client.prepareStart(False, legacySnapshotPolicy="NONE") == runtime.runtimeInstanceId
        assert client._presentationService is None
        assert client._presentationServer is None
        assert client.displayAddress() == ""
        assert getattr(runtime, "_presentationOwner", None) is None
    finally:
        client.close()


def testNoneWorkerReconcilesSameRequestAndPolicyWithoutRestart():
    calls, accepted, failures = [], [], []

    class Client:
        def prepareStart(self, capturePresentation, *, legacySnapshotPolicy):
            assert capturePresentation is False
            assert legacySnapshotPolicy == "NONE"
            return "instance"

        def startJob(self, projectId, **kwargs):
            calls.append(kwargs)
            raise TimeoutError("reply lost")

        def getStartRequest(self, requestId, generation):
            assert requestId == calls[0]["startRequestId"]
            assert generation == "instance"
            return pb.StartJobReply(ok=True, job_id="same-job", status="COMPLETED",
                start_request_id=requestId, runtime_instance_id=generation, legacy_snapshot_policy="NONE")

        def iterJobEvents(self, jobId, **kwargs):
            return iter(())

        def getJobStatus(self, jobId):
            return pb.GetJobStatusReply(ok=True, status="COMPLETED", legacy_snapshot_policy="NONE")

    worker = RuntimeWorker(Client(), "project", legacySnapshotPolicy="NONE")
    worker.jobAccepted.connect(accepted.append)
    worker.failed.connect(failures.append)
    _runWorker(worker)
    assert len(calls) == len(accepted) == 1
    assert calls[0]["legacySnapshotPolicy"] == accepted[0].legacy_snapshot_policy == "NONE"
    assert failures == []


def testNoneWorkerRejectsLegacyPrepareBeforeAnyStart():
    calls, failures = [], []
    client = SimpleNamespace(prepareStart=lambda capture: "instance",
                             startJob=lambda *args, **kwargs: calls.append(kwargs))
    worker = RuntimeWorker(client, "project", legacySnapshotPolicy="NONE")
    worker.failed.connect(failures.append)
    _runWorker(worker)
    assert not calls and failures
    assert not worker.startUncertain


def testNoneWorkerRejectsUnsupportedStartSignatureBeforeUncertainLookup():
    calls, failures = [], []
    client = SimpleNamespace(prepareStart=lambda capture, **kwargs: "instance",
                             startJob=lambda projectId: calls.append(projectId))
    worker = RuntimeWorker(client, "project", legacySnapshotPolicy="NONE")
    worker.failed.connect(failures.append)
    _runWorker(worker)
    assert not calls and failures
    assert not worker.startUncertain


def testListingCarriesOriginAndSelectedJobWithoutChangingSpatialSource():
    requests = []

    def listing(request, context):
        requests.append(request)
        return pb.ListNodePreviewSourcesReply(job_id="job", legacy_snapshot_policy="ALL",
            capture_state="CURRENT_AVAILABLE", sources=[pb.PreviewSourceInfo(source_id="asset",
            source_kind="upstream", origin_job_id="job", origin_project_revision=7,
            capture_id="capture", created_at_ms=123, snapshot_state="CURRENT")])

    client = RuntimeClient(SimpleNamespace(ListNodePreviewSources=listing))
    metadata = client.listNodePreviewSourcesWithMetadata("p", "w", "n", jobId="job")
    assert requests[0].job_id == metadata.jobId == "job"
    assert metadata.captureState == "CURRENT_AVAILABLE"
    source = metadata.sources[0]
    assert (source.originJobId, source.originProjectRevision, source.captureId, source.createdAtMs) == (
        "job", 7, "capture", 123)
    assert source.snapshotState == "CURRENT" and source.sourceKind == "upstream"
    assert client.listNodePreviewSources("p", "w", "n", jobId="job") == list(metadata.sources)


@pytest.mark.parametrize("selected,replyJob,origin,policy,state", [
    ("", "job", "job", "ALL", "CURRENT_AVAILABLE"),
    ("job", "", "job", "ALL", "CURRENT_AVAILABLE"),
    ("job", "other", "job", "ALL", "CURRENT_AVAILABLE"),
    ("job", "job", "", "ALL", "CURRENT_AVAILABLE"),
    ("job", "job", "other", "ALL", "CURRENT_AVAILABLE"),
    ("job", "job", "job", "", "CURRENT_AVAILABLE"),
    ("job", "job", "job", "NONE", "DISABLED_THIS_RUN"),
    ("job", "job", "job", "ALL", ""),
])
def testMissingOrMismatchedMetadataNeverClaimsCurrent(selected, replyJob, origin, policy, state):
    reply = pb.ListNodePreviewSourcesReply(job_id=replyJob, legacy_snapshot_policy=policy,
        capture_state=state, sources=[pb.PreviewSourceInfo(source_id="asset", source_kind="current",
        origin_job_id=origin, capture_id="capture", snapshot_state="CURRENT")])
    client = RuntimeClient(SimpleNamespace(ListNodePreviewSources=lambda request, context: reply))
    metadata = client.listNodePreviewSourcesWithMetadata("p", "w", "n", jobId=selected)
    assert metadata.sources[0].snapshotState == "UNKNOWN"
    assert metadata.sources[0].sourceKind == "current"


def testContextSuppliesCurrentJobCallbackOnEveryRefresh():
    from emo_master.apps.designer.operator_editors.controller_protocol import EditorContext, EditorKey
    current, calls = ["first"], []

    def listing(*args, **kwargs):
        calls.append(kwargs["jobId"])
        return PreviewSourceListing()

    context = EditorContext(key=EditorKey("project", "main", "node"), operatorId="test",
        version="1", previewMode="pure", paramSchema={},
        runtimeClient=SimpleNamespace(listNodePreviewSourcesWithMetadata=listing),
        applyParams=lambda *args: True, appendLog=lambda *args: None,
        getCurrentJobId=lambda: current[0])
    context.listPreviewSources()
    current[0] = "second"
    context.listPreviewSourcesWithMetadata()
    assert calls == ["first", "second"]


def _source(state="CURRENT", origin="job", asset="asset"):
    return PreviewSource(asset, "node.image", "current", "main", "node", "image", 1, 1,
                         "image/png", originJobId=origin, captureId="capture", snapshotState=state)


@pytest.fixture
def previewController():
    pytest.importorskip("PySide2")
    from PySide2.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QComboBox, QPushButton
    from emo_master.plugins.builtins._editor_support import PurePreviewControllerBase
    root = QWidget()
    layout, controls = QVBoxLayout(root), QHBoxLayout()
    combo, local = QComboBox(root), QPushButton("local", root)
    combo.setObjectName("sourceCombo")
    local.setObjectName("localImageButton")
    controls.addWidget(combo)
    controls.addWidget(local)
    layout.addLayout(controls)
    controller = PurePreviewControllerBase()
    controller.collectParams = lambda: {}
    controller.handled = []
    controller.handlePreviewResult = lambda outputs, assets: controller.handled.append(outputs) if outputs else None
    controller.statuses, controller.downloads, controller.cancelled = [], [], []
    controller.listing = PreviewSourceListing()
    controller.context = SimpleNamespace(
        bindPreviewInvalidation=lambda callback: None, currentJobId=lambda: "job",
        listPreviewSourcesWithMetadata=lambda: controller.listing,
        setStatus=controller.statuses.append, setError=controller.statuses.append,
        downloadPreviewAsset=lambda asset: (controller.downloads.append(asset) or b"synthetic-image", "image/png"),
        cancelPurePreview=controller.cancelled.append, log=lambda *args: None,
        markDirty=lambda: None,
    )
    controller.bindPreviewBase(root, controller.context)
    try:
        yield controller
    finally:
        from PySide2.QtCore import QCoreApplication, QEvent
        import shiboken2
        controller.disposePreviewBase()
        root.close()
        # Keep controller/child wrappers alive until their native timer/widget
        # ownership has retired on the GUI thread, before fixture-wide cleanup.
        root.deleteLater()
        QCoreApplication.sendPostedEvents(root, QEvent.DeferredDelete)
        assert not shiboken2.isValid(root)


def testBuiltinOnlyAutoSelectsVerifiedCurrentAndKeepsHistoricalChoices(previewController):
    controller = previewController
    controller.listing = PreviewSourceListing(sources=(
        _source("PREVIOUS", "old", "old-asset"), _source("UNKNOWN", "", "unknown"), _source()),
        jobId="job", legacySnapshotPolicy="ALL", captureState="CURRENT_AVAILABLE")
    controller.refreshSources()
    assert controller.currentAssetId == "asset"
    assert controller.downloads == ["asset"]
    assert "历史快照" in controller.sourceCombo.itemText(1)
    assert "来源未知" in controller.sourceCombo.itemText(2)
    assert "本次运行" in controller.sourceCombo.currentText()
    controller.sourceCombo.setCurrentIndex(1)
    assert controller.currentAssetId == "old-asset"
    assert controller.downloads == ["asset", "old-asset"]


def testNoneAndUnknownNeverAutoSelectHistoricalSource(previewController):
    controller = previewController
    controller.listing = PreviewSourceListing(sources=(_source("PREVIOUS", "old"),),
        jobId="job", legacySnapshotPolicy="NONE", captureState="DISABLED_THIS_RUN")
    controller.refreshSources()
    assert controller.currentAssetId == "" and not controller.downloads
    assert controller.statuses[-1] == "本次运行未采集节点调试快照"
    assert "历史快照" in controller.sourceCombo.itemText(1)
    controller.listing = PreviewSourceListing(sources=(_source(),))
    controller.refreshSources()
    assert controller.currentAssetId == "" and not controller.downloads
    assert "来源未知" in controller.sourceCombo.itemText(1)


def testInspectorInvalidationClearsSelectionCancelsAndDropsLateResult(previewController):
    controller = previewController
    controller.listing = PreviewSourceListing(sources=(_source(),), jobId="job",
        legacySnapshotPolicy="ALL", captureState="CURRENT_AVAILABLE")
    controller.refreshSources()
    oldGeneration = controller._generation
    controller._requestId = "old-preview"
    assert controller._debounceTimer.isActive()
    controller.invalidatePreviewSources()
    assert controller.currentAssetId == ""
    assert controller.cancelled == ["old-preview"]
    assert not controller._debounceTimer.isActive()
    controller._results.put((oldGeneration, SimpleNamespace(ok=True, outputs_json='{"stale":true}', assets=[]), None))
    controller._pollResults()
    assert controller.handled == []
    assert controller.sourceCombo.currentData() == ""


@pytest.mark.parametrize("selection", ["placeholder", "expired", "empty-reply"])
def testSourceSelectionClearsHistoricalPixelsAndOutputsBeforeFailure(previewController, selection):
    from PySide2.QtGui import QColor, QPixmap
    from PySide2.QtWidgets import QLabel

    controller = previewController
    sourceImage, outputImage = QLabel(controller.root), QLabel(controller.root)
    controller.root.layout().addWidget(sourceImage)
    controller.root.layout().addWidget(outputImage)
    historicalPixmap = QPixmap(2, 2)
    historicalPixmap.fill(QColor("red"))
    chart = {}

    def showSource(content, mimeType):
        sourceImage.setPixmap(historicalPixmap if content else QPixmap())

    def showResults(outputs, assets):
        chart.clear()
        chart.update(outputs)
        # Keep the companion pixmap on empty outputs, as ROI's handler does;
        # the shared visual-clear helper must explicitly clear these labels.
        if outputs:
            outputImage.setPixmap(historicalPixmap)

    controller.onSourceImage = showSource
    controller.handlePreviewResult = showResults
    controller.listing = PreviewSourceListing(sources=(
        _source("PREVIOUS", "old", "historical"), _source("CURRENT", "job", "current")),
        jobId="job", legacySnapshotPolicy="ALL", captureState="CURRENT_AVAILABLE")
    controller.refreshSources()
    controller.sourceCombo.setCurrentIndex(1)
    controller.handlePreviewResult({"histogram": {"historical": True}}, {})
    oldGeneration = controller._generation
    controller._requestId = "historical-request"
    itemCount = controller.sourceCombo.count()
    assert controller.currentAssetId == "historical"
    assert not sourceImage.pixmap().isNull()
    assert not outputImage.pixmap().isNull()
    assert chart

    def failDownload(assetId):
        assert assetId == "current"
        if selection == "empty-reply":
            return b"", ""
        raise RuntimeError("selected current snapshot expired")

    controller.context.downloadPreviewAsset = failDownload
    controller.sourceCombo.setCurrentIndex(0 if selection == "placeholder" else 2)
    assert controller.sourceCombo.count() == itemCount
    assert controller.currentAssetId == ""
    assert sourceImage.pixmap() is None or sourceImage.pixmap().isNull()
    assert outputImage.pixmap() is None or outputImage.pixmap().isNull()
    assert not chart
    assert not controller._debounceTimer.isActive()
    assert controller.cancelled[-1] == "historical-request"
    if selection == "placeholder":
        assert controller.statuses[-1] == "请选择快照或本地图片"
    else:
        assert "本次运行" in controller.sourceCombo.currentText()
        assert "expired" in controller.statuses[-1] or "已失效" in controller.statuses[-1]
    controller._results.put((oldGeneration,
        SimpleNamespace(ok=True, outputs_json='{"historical":true}', assets=[]), None))
    controller._pollResults()
    assert not chart
    assert outputImage.pixmap() is None or outputImage.pixmap().isNull()


def testDisposedInspectorUnregistersInvalidationWithoutTouchingNativeChildren(previewController):
    controller = previewController
    bindings = []
    controller.context.bindPreviewInvalidation = bindings.append
    controller.disposePreviewBase()
    assert len(bindings) == 1
    controller.sourceCombo = object()  # Would fail if a late callback touched Qt.
    bindings[0]()
    controller.invalidatePreviewSources()
    controller._pollResults()
    assert controller.currentAssetId == ""


def testMainWindowNextRunChoiceIsFrozenAndInspectorsInvalidate(monkeypatch):
    pytest.importorskip("PySide2")
    import shiboken2
    from PySide2.QtCore import QCoreApplication, QEvent
    from emo_master.apps.designer.ui.main_window import MainWindow
    import emo_master.apps.designer.controllers.runtime_controller as module

    class Signal:
        def connect(self, callback):
            return None

    class Worker:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)
            self.jobAccepted, self.eventReceived = Signal(), Signal()
            self.statusChanged, self.failed = Signal(), Signal()

        def start(self):
            return None

        def isRunning(self):
            return False

        def requestStop(self):
            return None

    # Exercise real UI/controller construction without starting a QThread/job.
    monkeypatch.setattr(module, "RuntimeWorker", Worker)
    window = MainWindow(SimpleNamespace(listOperators=lambda: []))
    scene = window.flowScene
    try:
        assert window.nextRunLegacySnapshotPolicy == "ALL"
        assert window.legacySnapshotPolicyCombo.currentData() == "ALL"
        window.loadedProjectPath = "project"
        window.runtimeController.syncRuntimeProjectBeforeRun = lambda: True
        window.runtimeController.getCapturePresentation = lambda: False
        invalidations = []
        window.operatorEditorManager.invalidatePreviewSources = lambda: invalidations.append(window.currentJobId)
        initialPayload = window.workflowStore.toProjectPayload()
        window.setNextRunLegacySnapshotPolicy("NONE")
        assert window.workflowStore.toProjectPayload() == initialPayload
        window.startJob()
        worker = window.runtimeController._worker
        assert worker.legacySnapshotPolicy == "NONE"
        window.setNextRunLegacySnapshotPolicy("ALL")
        assert worker.legacySnapshotPolicy == "NONE"
        assert invalidations == [None]
        window.runtimeController._onJobAccepted(pb.StartJobReply(ok=True, job_id="new-job",
            status="COMPLETED", legacy_snapshot_policy="NONE"))
        assert invalidations == [None, "new-job"]
        window.runtimeController._onJobStatus(pb.GetJobStatusReply(ok=True, status="COMPLETED"))
        window.runtimeController._onWorkerFinished(worker)
    finally:
        # Synthetic replies have no live Runtime job to stop. Retire the scene
        # and parent while their Python wrappers remain strongly held here.
        window.runtimeController._onJobStatus(pb.GetJobStatusReply(ok=True, status="COMPLETED"))
        window.close()
        window.flowView.setScene(None)
        scene.clearGraph()
        scene.deleteLater()
        QCoreApplication.sendPostedEvents(scene, QEvent.DeferredDelete)
        assert not shiboken2.isValid(scene)
        window.deleteLater()
        QCoreApplication.sendPostedEvents(window, QEvent.DeferredDelete)
        assert not shiboken2.isValid(window)
