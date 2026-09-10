from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
import json
from pathlib import Path
import queue
from typing import Any, cast
from uuid import uuid4

from PySide2.QtCore import QByteArray, QObject, QTimer, Qt
from PySide2.QtGui import QImage, QPixmap
from PySide2.QtWidgets import QComboBox, QFileDialog, QLabel, QPushButton


def requiredChild(root: object, widgetType, objectName: str):
    findChild = getattr(root, "findChild", None)
    child = findChild(widgetType, objectName) if callable(findChild) else None
    if child is None:
        raise RuntimeError(f"editor UI is missing objectName '{objectName}'")
    return child


def setImageLabel(label: QLabel, content: bytes, fallbackText: str = "图像不可用") -> None:
    image = QImage.fromData(QByteArray(content))
    if image.isNull():
        label.setPixmap(QPixmap())
        label.setText(fallbackText)
        return
    pixmap = QPixmap.fromImage(image)
    from emo_master.apps.designer.ui.widgets import PreviewLabel
    if isinstance(label, PreviewLabel):
        label.setPixmap(pixmap)
        return
    target = label.size()
    if target.width() > 1 and target.height() > 1:
        pixmap = pixmap.scaled(
            target,
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
    label.setText("")
    label.setPixmap(pixmap)


class PurePreviewControllerBase:
    def __init__(self) -> None:
        self.root: Any = None
        self.context: Any = None
        self.sourceCombo: QComboBox | None = None
        self.localImageButton: QPushButton | None = None
        self.currentAssetId = ""
        self._sourceMimeTypes: dict[str, str] = {}
        self._generation = 0
        self._executor = ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="designer-operator-preview"
        )
        self._future: Future[object] | None = None
        self._requestId = ""
        self._results: queue.Queue[tuple[int, object | None, BaseException | None]] = (
            queue.Queue()
        )
        self._debounceTimer: QTimer | None = None
        self._pollTimer: QTimer | None = None
        self._disposed = False

    def bindPreviewBase(self, root: object, context: Any) -> None:
        self.root = root
        self.context = context
        self.sourceCombo = requiredChild(root, QComboBox, "sourceCombo")
        self.localImageButton = requiredChild(
            root, QPushButton, "localImageButton"
        )
        self.sourceCombo.currentIndexChanged.connect(self._sourceChanged)
        self.localImageButton.clicked.connect(self._chooseLocalImage)
        self._debounceTimer = QTimer(cast(QObject, root))
        self._debounceTimer.setSingleShot(True)
        self._debounceTimer.setInterval(250)
        self._debounceTimer.timeout.connect(self._startPreview)
        self._pollTimer = QTimer(cast(QObject, root))
        self._pollTimer.setInterval(50)
        self._pollTimer.timeout.connect(self._pollResults)
        self._pollTimer.start()

    def onOpen(self) -> None:
        self.refreshSources()

    def onClose(self) -> None:
        return

    def refreshSources(self) -> None:
        if self.sourceCombo is None or self.context is None:
            return
        combo = self.sourceCombo
        combo.blockSignals(True)
        combo.clear()
        self._sourceMimeTypes.clear()
        try:
            sources = self.context.listPreviewSources()
        except Exception as err:
            sources = []
            self.context.setError(str(err))
        for source in sources:
            assetId = str(getattr(source, "sourceId", ""))
            if assetId:
                combo.addItem(str(getattr(source, "label", assetId)), assetId)
                self._sourceMimeTypes[assetId] = str(
                    getattr(source, "mimeType", "")
                )
        combo.blockSignals(False)
        if combo.count() > 0:
            combo.setCurrentIndex(0)
            self._sourceChanged(0)
        else:
            self.currentAssetId = ""
            self.context.setStatus("暂无作业快照，请选择本地图片")

    def schedulePreview(self, *, markDirty: bool = True) -> None:
        if self._disposed or self._debounceTimer is None:
            return
        if markDirty and self.context is not None:
            self.context.markDirty()
        self._cancelCurrentPreview()
        self._generation += 1
        self._debounceTimer.start()

    def _chooseLocalImage(self) -> None:
        if self.context is None or self.sourceCombo is None:
            return
        filename, _ = QFileDialog.getOpenFileName(
            self.root,
            "选择预览图片",
            "",
            "图像 (*.png *.jpg *.jpeg *.bmp *.tif *.tiff);;所有文件 (*.*)",
        )
        if not filename:
            return
        try:
            data = Path(filename).read_bytes()
            assetId = self.context.uploadPreviewImage(data, Path(filename).name)
        except Exception as err:
            self.context.setError(str(err))
            return
        self.sourceCombo.addItem(f"本地：{Path(filename).name}", assetId)
        self.sourceCombo.setCurrentIndex(self.sourceCombo.count() - 1)

    def _sourceChanged(self, _index: int) -> None:
        if self.sourceCombo is None or self.context is None:
            return
        assetId = self.sourceCombo.currentData()
        self.currentAssetId = str(assetId) if assetId is not None else ""
        if not self.currentAssetId:
            return
        try:
            content, mimeType = self.context.downloadPreviewAsset(self.currentAssetId)
            if mimeType.startswith("image/"):
                self.onSourceImage(content, mimeType)
            else:
                self.onStructuredSource(content, mimeType)
        except Exception as err:
            self.context.setError(str(err))
            return
        if mimeType.startswith("image/"):
            self.schedulePreview(markDirty=False)

    def _startPreview(self) -> None:
        if self._disposed or self.context is None or not self.currentAssetId:
            return
        generation = self._generation
        params = self.collectParams()
        previous = self._future
        if previous is not None and not previous.done():
            previous.cancel()
        requestId = str(uuid4())
        self._requestId = requestId
        future = self._executor.submit(
            self.context.runPurePreview,
            params,
            self.currentAssetId,
            requestId,
        )
        self._future = future

        def completed(value: Future[object]) -> None:
            try:
                self._results.put((generation, value.result(), None))
            except BaseException as err:
                self._results.put((generation, None, err))

        future.add_done_callback(completed)

    def _pollResults(self) -> None:
        latest: tuple[int, object | None, BaseException | None] | None = None
        while True:
            try:
                latest = self._results.get_nowait()
            except queue.Empty:
                break
        if latest is None or latest[0] != self._generation or self.context is None:
            return
        _generation, reply, error = latest
        if error is not None:
            self.context.setError(str(error))
            return
        if not bool(getattr(reply, "ok", False)):
            code = str(getattr(reply, "code", ""))
            message = str(getattr(reply, "message", ""))
            self.context.setError(f"{code}: {message}".strip(": "))
            return
        rawOutputs = str(getattr(reply, "outputs_json", "{}"))
        try:
            parsed = json.loads(rawOutputs)
        except json.JSONDecodeError:
            parsed = {}
        outputs = parsed if isinstance(parsed, dict) else {}
        assets = {
            str(getattr(asset, "port", "")): str(getattr(asset, "asset_id", ""))
            for asset in getattr(reply, "assets", [])
            if str(getattr(asset, "port", ""))
            and str(getattr(asset, "asset_id", ""))
        }
        self.handlePreviewResult(outputs, assets)

    def disposePreviewBase(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        if self._debounceTimer is not None:
            self._debounceTimer.stop()
        if self._pollTimer is not None:
            self._pollTimer.stop()
        if self._future is not None:
            self._future.cancel()
        self._cancelCurrentPreview()
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _cancelCurrentPreview(self) -> None:
        requestId = self._requestId
        self._requestId = ""
        if not requestId or self.context is None:
            return
        try:
            self.context.cancelPurePreview(requestId)
        except Exception as err:
            self.context.log("WARN", f"取消旧预览失败：{err}")

    def collectParams(self) -> dict[str, object]:
        raise NotImplementedError

    def onSourceImage(self, content: bytes, mimeType: str) -> None:
        _ = content, mimeType

    def onStructuredSource(self, content: bytes, mimeType: str) -> None:
        _ = content, mimeType

    def handlePreviewResult(
        self,
        outputs: dict[str, object],
        assets: dict[str, str],
    ) -> None:
        _ = outputs, assets
