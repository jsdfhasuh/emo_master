from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading
import time

import numpy as np

from emo_master.apps.runtime.preview.executor import PurePreviewExecutor
from emo_master.apps.runtime.preview.store import PreviewAssetStore
from emo_master.core.plugin.models import (
    OperatorEditorSpec,
    PluginDescriptor,
    PluginManifest,
)


class _SlowPureOperator:
    started = threading.Event()
    cancellationSeen = threading.Event()

    def validateParams(self, _params: dict[str, object]):
        return None

    def executeNode(self, _inputs, _params, context):
        self.started.set()
        checkCancellation = context["raiseIfCancellationRequested"]
        while True:
            try:
                checkCancellation()
            except RuntimeError:
                self.cancellationSeen.set()
                return {
                    "status": "ok",
                    "outputs": {
                        "image": np.zeros((4, 4, 3), dtype=np.uint8),
                    },
                }
            time.sleep(0.005)


def _descriptor() -> PluginDescriptor:
    return PluginDescriptor(
        manifest=PluginManifest(
            operatorId="vision.test.slow_preview",
            displayName="Slow Preview",
            version="1.0.0",
            entry="tests.fake:SlowPureOperator",
            category="test",
            iconKey="test",
            summary="",
            inputPorts={"image": "image"},
            outputPorts={"image": "image"},
            paramSchema={"type": "object"},
            minCoreVersion="0.1.0",
            maxCoreVersion="1.x",
            editor=OperatorEditorSpec(
                schemaVersion="1.0",
                kind="customUi",
                openMode="window",
                uiResource="ui/editor.ui",
                controllerEntry="tests.fake:Controller",
                fallback="schemaForm",
                previewMode="pure",
            ),
        ),
        operatorClass=_SlowPureOperator,
    )


def testPurePreviewTimeoutSignalsCancellationAndLeavesNoLateAssets(
    tmp_path: Path,
) -> None:
    _SlowPureOperator.cancellationSeen.clear()
    _SlowPureOperator.started.clear()
    store = PreviewAssetStore(tmp_path / "preview-cache")
    source = store.addTransientImage(
        np.zeros((8, 8, 3), dtype=np.uint8),
        projectKey="project-key",
    )
    executor = PurePreviewExecutor(
        {"vision.test.slow_preview": _descriptor()},
        store,
    )
    try:
        result = executor.execute(
            "vision.test.slow_preview",
            {},
            source.assetId,
            projectId="project-a",
            projectKey="project-key",
            timeoutSeconds=0.1,
        )
        assert result.ok is False
        assert result.code == "E_PREVIEW_TIMEOUT"
        assert _SlowPureOperator.cancellationSeen.wait(1.0)
        deadline = time.monotonic() + 1.0
        while len(list(store.transientRoot.glob("*.png"))) != 1 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert len(list(store.transientRoot.glob("*.png"))) == 1
    finally:
        executor.close()
        store.close()


def testPurePreviewCanBeCancelledByRequestId(tmp_path: Path) -> None:
    _SlowPureOperator.cancellationSeen.clear()
    _SlowPureOperator.started.clear()
    store = PreviewAssetStore(tmp_path / "preview-cache")
    source = store.addTransientImage(
        np.zeros((8, 8, 3), dtype=np.uint8),
        projectKey="project-key",
    )
    executor = PurePreviewExecutor(
        {"vision.test.slow_preview": _descriptor()},
        store,
    )
    try:
        with ThreadPoolExecutor(max_workers=1) as caller:
            future = caller.submit(
                executor.execute,
                "vision.test.slow_preview",
                {},
                source.assetId,
                projectId="project-a",
                projectKey="project-key",
                timeoutSeconds=2.0,
                requestId="request-1",
            )
            assert _SlowPureOperator.started.wait(1.0)
            assert executor.cancel("request-1") is True
            result = future.result(timeout=1.0)
        assert result.ok is False
        assert result.code == "E_CANCELLED"
        assert _SlowPureOperator.cancellationSeen.is_set()
        assert executor.cancel("request-1") is False
    finally:
        executor.close()
        store.close()
