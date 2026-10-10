import hashlib
import threading
import time

import pytest

from emo_master.apps.designer.services.operator_catalog_worker import OperatorCatalogWorker
from emo_master.apps.designer.services.operator_icon_cache import IconRequest, validateIconReply

pytest.importorskip("PySide2.QtWidgets")
from PySide2.QtGui import QImage
from PySide2.QtWidgets import QLabel, QListWidgetItem
import shiboken2

from emo_master.apps.designer.ui.operator_icon_provider import OperatorIconProvider, renderIconAsset
from tests.designer.qt_wait import waitUntil
from tests.icon_fixtures import SVG, iconDefinition, iconReply, png


class Client:
    runtimeScope = "s"

    def __init__(self):
        self.calls = []

    def getOperatorIconAsset(self, operatorId, *_, **kwargs):
        self.calls.append(operatorId)
        return iconReply()


@pytest.mark.parametrize("dpr", [1, 1.5, 2])
@pytest.mark.parametrize("content,mime", [(SVG, "image/svg+xml"), (png(), "image/png")])
def testRealRenderingHasCustomPixelsAtEachDpi(content, mime, dpr):
    request = IconRequest.fromDefinition("s", 1, iconDefinition(content, mimeType=mime))
    asset = validateIconReply(request, iconReply(content, mimeType=mime))
    rendered = renderIconAsset(asset, 20, dpr)
    assert rendered.source == "custom"
    assert rendered.sha256 == hashlib.sha256(content).hexdigest()
    assert rendered.pixelSize == int(20 * dpr)
    assert rendered.nonTransparentPixels > 100
    image = rendered.icon.pixmap(rendered.pixelSize, rendered.pixelSize).toImage()
    center = image.pixelColor(image.width() // 2, image.height() // 2)
    assert center.name() == ("#e23456" if mime == "image/svg+xml" else "#ffffff")
    assert renderIconAsset(asset, 512, 8).pixelSize == 512


def testQtRenderingCannotRunOnAWorker():
    request = IconRequest.fromDefinition("s", 1, iconDefinition())
    asset = validateIconReply(request, iconReply())
    errors = []

    def render():
        try:
            renderIconAsset(asset)
        except RuntimeError as err:
            errors.append(str(err))

    thread = threading.Thread(target=render)
    thread.start()
    thread.join(1)
    assert errors == ["icon rendering requires the GUI thread"]


def testSharedSubscribersRebindingAndDeletionNeverReceiveOldResults():
    release = threading.Event()
    started = threading.Event()
    green = SVG.replace(b"#e23456", b"#12ab45")

    class SlowClient(Client):
        def getOperatorIconAsset(self, operatorId, *_, **kwargs):
            self.calls.append(operatorId)
            if operatorId == "red":
                started.set()
                release.wait(3)
            return iconReply(SVG if operatorId == "red" else green)

    client = SlowClient()
    provider = OperatorIconProvider(client)
    first, shared, deleted = QLabel(), QLabel(), QLabel()
    try:
        provider.setCatalog([iconDefinition(operatorId="red"), iconDefinition(green, operatorId="green")])
        oldToken = provider.bind(first, "red", mode="label", context=("p1", "w1", "n1"))
        provider.bind(shared, "red", mode="label")
        provider.bind(deleted, "red", mode="label")
        assert started.wait(1)
        newToken = provider.bind(first, "green", mode="label", context=("p2", "w2", "n1"))
        assert newToken > oldToken
        shiboken2.delete(deleted)
        waitUntil(lambda: first._operatorIconSource == "custom")
        release.set()
        waitUntil(lambda: shared._operatorIconSource == "custom")
        assert first._operatorIconSha == hashlib.sha256(green).hexdigest()
        assert shared._operatorIconSha == hashlib.sha256(SVG).hexdigest()
        assert client.calls.count("red") == 1
        assert first.pixmap().toImage().pixelColor(10, 10).name() == "#12ab45"
    finally:
        release.set()
        assert provider.close()


def testReusedResourceAvoidsRedownloadAndInvalidCatalogRevokesIt():
    client = Client()
    provider = OperatorIconProvider(client)
    target = QListWidgetItem("unhashable Qt target")
    try:
        definition = iconDefinition()
        provider.setCatalog([definition])
        token = provider.bind(target, "test.icon", mode="list")
        waitUntil(lambda: target._operatorIconSource == "custom")
        provider.setCatalog([definition])
        waitUntil(lambda: target._operatorIconSource == "custom")
        assert len(client.calls) == 1
        assert provider._targets[id(target)] > token
        provider.setCatalog([{**definition, "icon": {"status": "invalid"}}])
        assert target._operatorIconSource == "fallback" and target._operatorIconSha == ""
        assert not target.icon().isNull()
        assert len(client.calls) == 1
    finally:
        assert provider.close()


def testTransientFailureRetriesOnlyOnceUntilExplicitReset():
    class Offline(Client):
        def getOperatorIconAsset(self, *_, **kwargs):
            self.calls.append(1)
            raise OSError("offline")

    client = Offline()
    provider = OperatorIconProvider(client)
    target = QLabel()
    try:
        provider.setCatalog([iconDefinition()])
        provider.bind(target, "test.icon", mode="label")
        waitUntil(lambda: bool(provider._failures))
        for failure in provider._failures.values():
            failure.retryAt = 0
        waitUntil(lambda: any(f.attempts == 2 for f in provider._failures.values()))
        for failure in provider._failures.values():
            failure.retryAt = 0
        for _ in range(20):
            provider.poll()
        assert len(client.calls) == 2
        assert target._operatorIconSource == "fallback"
        provider.resetFailures()
        waitUntil(lambda: len(client.calls) == 3)
    finally:
        assert provider.close()


def testUnimplementedIsRememberedForSessionAndRecoveryIsNotRecursive():
    class Unsupported(Client):
        def getOperatorIconAsset(self, *_, **kwargs):
            self.calls.append(1)
            return iconReply(ok=False, code="UNIMPLEMENTED", message="old Runtime")

    client = Unsupported()
    recovery = []
    provider = OperatorIconProvider(client, onRecovery=lambda: recovery.append(1))
    target = QLabel()
    try:
        definition = iconDefinition()
        provider.setCatalog([definition])
        provider.bind(target, "test.icon", mode="label")
        waitUntil(lambda: provider._unsupported)
        provider.resetFailures()
        provider.setCatalog([definition])
        for _ in range(10):
            provider.poll()
        assert client.calls == [1]
        client.runtimeScope = "new"
        provider.setScope("new")
        provider.setCatalog([definition])
        waitUntil(lambda: len(client.calls) == 2)
        assert recovery == []
    finally:
        assert provider.close()


def testDigestRecoveryBudgetSurvivesNewCatalogGenerations():
    class Mismatch(Client):
        def getOperatorIconAsset(self, *_, **kwargs):
            self.calls.append(1)
            return iconReply(sha256="0" * 64)

    client = Mismatch()
    recovery = []
    logs = []
    provider = OperatorIconProvider(client, onRecovery=lambda: recovery.append(1), appendLog=lambda *v: logs.append(v))
    target = QLabel()
    try:
        definition = iconDefinition()
        provider.setCatalog([definition])
        provider.bind(target, "test.icon", mode="label")
        waitUntil(lambda: bool(provider._failures))
        provider.setCatalog([definition])
        waitUntil(lambda: any(f.generation == 2 for f in provider._failures.values()))
        assert len(recovery) == 1 and len(client.calls) == 2
        assert len(logs) == 1
        provider.resetFailures()
        provider.setCatalog([definition])
        waitUntil(lambda: len(recovery) == 2)
    finally:
        assert provider.close()


def testShutdownUsesOneBudgetAndDoesNotPretendLocalMethodStopped():
    release = threading.Event()
    starts = []

    class Stuck(Client):
        def listOperators(self, **kwargs):
            starts.append("catalog")
            release.wait(3)
            return []

        def getOperatorIconAsset(self, *_, **kwargs):
            starts.append("icon")
            release.wait(3)
            return iconReply()

    client = Stuck()
    logs = []
    catalog = OperatorCatalogWorker(client)
    provider = OperatorIconProvider(client, appendLog=lambda *args: logs.append(args))
    target = QLabel()
    try:
        catalog.refresh("s")
        provider.setCatalog([iconDefinition()])
        provider.bind(target, "test.icon", mode="label")
        waitUntil(lambda: len(starts) == 2)
        start = time.monotonic()
        assert not provider.close(catalog, timeout=0.05)
        assert time.monotonic() - start < 0.4
        assert provider.worker.isRunning() and catalog.isRunning()
        assert any("E_DISPLAY_SHUTDOWN_TIMEOUT" in entry[1] for entry in logs)
        assert provider.bind(target, "test.icon", mode="label") == 0
    finally:
        release.set()
        assert provider.worker.wait(1) and catalog.wait(1)


def testTransparentImagesAreNotReportedAsCustomSuccess():
    content = png(raw=b"\0" * 18)
    request = IconRequest.fromDefinition("s", 1, iconDefinition(content, mimeType="image/png"))
    asset = validateIconReply(request, iconReply(content, mimeType="image/png"))
    with pytest.raises(ValueError, match="no visible pixels"):
        renderIconAsset(asset)


def testRepeatedCloseStillReportsPendingCatalogWorker():
    release, started = threading.Event(), threading.Event()

    class SlowCatalog(Client):
        def listOperators(self, **kwargs):
            started.set()
            release.wait(3)
            return []

    client = SlowCatalog()
    catalog = OperatorCatalogWorker(client)
    provider = OperatorIconProvider(client)
    try:
        catalog.refresh("s")
        assert started.wait(1)
        assert not provider.close(catalog, timeout=0.03)
        assert provider.worker.wait(1)
        assert not provider.close()
        assert provider._renderedBytes == 0
        release.set()
        assert catalog.wait(1)
        assert provider.close()
    finally:
        release.set()
        assert catalog.wait(1) and provider.worker.wait(1)


def testPngImageBytesAreActuallyRendered():
    content = png(width=2, height=1, raw=b"\0\xff\0\0\xff\0\xff\0\xff")
    request = IconRequest.fromDefinition("s", 1, iconDefinition(content, mimeType="image/png"))
    rendered = renderIconAsset(validateIconReply(request, iconReply(content, mimeType="image/png")), 20)
    image = rendered.icon.pixmap(20, 20).toImage().convertToFormat(QImage.Format_RGBA8888)
    assert image.pixelColor(2, 10).red() > 240
    assert image.pixelColor(17, 10).green() > 240
    assert image.pixelColor(10, 0).alpha() == 0


@pytest.mark.parametrize("size,count,retained", [(8, 260, 256), (512, 35, 32)])
def testRenderCacheEnforcesItemAndByteLimits(size, count, retained):
    client = Client()
    provider = OperatorIconProvider(client)
    target = QLabel()
    provider._timer.stop()
    try:
        for index in range(count):
            content = SVG.replace(b"#e23456", f"#{index:06x}".encode())
            definition = iconDefinition(content)
            request = IconRequest.fromDefinition("s", index + 1, definition)
            provider.cache.put("s", validateIconReply(request, iconReply(content)))
            provider.setCatalog([definition])
            token = provider.bind(target, "test.icon", mode="label", size=size)
            provider._ensure(provider._bindings[token])
            assert target._operatorIconSource == "custom"
            assert len(provider._rendered) <= 256
            assert provider._renderedBytes <= 32 * 1024 * 1024
        assert len(provider._rendered) == retained
        assert provider._renderedBytes == sum(icon.pixelSize ** 2 * 4 for icon in provider._rendered.values())
        assert client.calls == []
        provider.setScope("new")
        assert not provider._rendered and provider._renderedBytes == 0
    finally:
        assert provider.close()
