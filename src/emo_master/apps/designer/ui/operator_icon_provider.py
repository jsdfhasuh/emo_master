from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import math
import struct
import time
from typing import Any
import weakref

from PySide2.QtCore import QByteArray, QCoreApplication, QEventLoop, QObject, QRectF, QThread, QTimer, Qt
from PySide2.QtGui import QIcon, QImage, QPainter, QPixmap
import shiboken2

from emo_master.apps.designer.services.operator_icon_cache import IconRequest, OperatorIconCache
from emo_master.apps.designer.services.operator_icon_worker import OperatorIconWorker
from emo_master.apps.designer.ui.icon_map import operatorIcon
from emo_master.core.plugin.icon_resources import IconValidationError
from emo_master.core.plugin.models import PluginIconAsset


@dataclass(frozen=True)
class RenderedIcon:
    icon: Any
    sha256: str
    pixelSize: int
    nonTransparentPixels: int
    source: str = "custom"


def renderIconAsset(asset: PluginIconAsset, logicalSize: int = 20, devicePixelRatio: float = 1.0) -> RenderedIcon:
    app = QCoreApplication.instance()
    if app is None or QThread.currentThread() != app.thread():
        raise RuntimeError("icon rendering requires the GUI thread")
    size = min(512, max(1, math.ceil(min(512, max(1, logicalSize)) * min(8, max(1, devicePixelRatio)))))
    image = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    try:
        if asset.mimeType == "image/svg+xml":
            from PySide2.QtSvg import QSvgRenderer
            renderer = QSvgRenderer(QByteArray(asset.content))
            if not renderer.isValid():
                raise ValueError("Qt rejected the SVG")
            renderer.setAspectRatioMode(Qt.KeepAspectRatio)
            renderer.render(painter, QRectF(0, 0, size, size))
        else:
            decoded = QImage.fromData(QByteArray(asset.content))
            if decoded.isNull() or (decoded.width(), decoded.height()) != struct.unpack_from(">II", asset.content, 16):
                raise ValueError("Qt PNG dimensions do not match validated IHDR")
            decoded = decoded.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            painter.drawImage((size - decoded.width()) // 2, (size - decoded.height()) // 2, decoded)
    finally:
        painter.end()
    pixels = image.convertToFormat(QImage.Format_RGBA8888)
    alpha = bytes(pixels.constBits())[3::4]
    visible = sum(value != 0 for value in alpha)
    if not visible:
        raise ValueError("custom icon rendered no visible pixels")
    image.setDevicePixelRatio(size / max(1, logicalSize))
    return RenderedIcon(QIcon(QPixmap.fromImage(image)), asset.sha256, size, visible)


@dataclass
class _Binding:
    token: int
    target: Any
    targetId: int
    operatorId: str
    context: tuple
    mode: str
    size: int
    priority: int
    request: IconRequest | None
    applied: tuple | None = None


@dataclass
class _Failure:
    code: str
    message: str
    attempts: int
    retryAt: float
    generation: int


class OperatorIconProvider(QObject):
    def __init__(self, runtimeClient, *, parent=None, onRecovery=None, appendLog=None) -> None:
        super().__init__(parent)
        self.runtimeClient = runtimeClient
        self.scope = str(getattr(runtimeClient, "runtimeScope", id(runtimeClient)))
        self.generation = 0
        self.catalog: dict[str, dict[str, object]] = {}
        self.cache = OperatorIconCache()
        self.worker = OperatorIconWorker(runtimeClient)
        self._onRecovery = onRecovery
        self._appendLog = appendLog
        self._bindings: dict[int, _Binding] = {}
        # QListWidgetItem supports weakrefs but is unhashable in PySide2.
        self._targets: dict[int, int] = {}
        self._serial = 0
        self._failures: dict[tuple, _Failure] = {}
        self._rendered: OrderedDict[tuple, RenderedIcon] = OrderedDict()
        self._renderedBytes = 0
        self._logged: set[tuple] = set()
        self._unsupported = False
        self._recoveryUsed = False
        self._closed = False
        self._closingWorkers: list[Any] = [self.worker]
        self._timer = QTimer(self)
        self._timer.setInterval(20)
        self._timer.timeout.connect(self.poll)
        self._timer.start()

    def setScope(self, scope: str) -> None:
        if scope == self.scope:
            return
        self.scope = scope
        self.cache.clear()
        self._rendered.clear()
        self._renderedBytes = 0
        self._logged.clear()
        self._unsupported = False
        self.resetFailures()
        self.setCatalog([])

    def resetFailures(self) -> None:
        self._failures.clear()
        self._recoveryUsed = False

    def setCatalog(self, catalog: list[dict[str, object]]) -> None:
        if self._closed:
            return
        self.catalog = {str(item["operatorId"]): item for item in catalog}
        self.generation += 1
        for binding in tuple(self._bindings.values()):
            target = binding.target()
            if self._valid(target):
                self.bind(target, binding.operatorId, context=binding.context, mode=binding.mode, size=binding.size, priority=binding.priority, force=True)
            else:
                self.unsubscribe(binding.token)
        for definition in catalog:
            issues = definition.get("iconIssues", ())
            if isinstance(issues, (list, tuple)):
                for issue in issues:
                    if isinstance(issue, dict):
                        self._diagnostic(str(definition["operatorId"]), str(issue.get("code", "ICON_RESOURCE")), str(issue.get("message", "")))

    def _valid(self, target) -> bool:
        if target is None:
            return False
        try:
            return shiboken2.isValid(target)
        except TypeError:
            return True

    def bind(self, target, operatorId: str, *, context: tuple = (), mode: str = "custom", size: int = 20, priority: int = 1, force: bool = False) -> int:
        if self._closed:
            return 0
        previous = self._bindings.get(self._targets.get(id(target), 0))
        if (not force and previous and previous.target() is target
                and previous.operatorId == operatorId and previous.context == context
                and (previous.mode, previous.size, previous.priority) == (mode, size, priority)):
            return previous.token
        if previous:
            self.unsubscribe(previous.token)
        self._serial += 1
        definition = self.catalog.get(operatorId, {})
        request = None
        try:
            request = IconRequest.fromDefinition(self.scope, self.generation, definition)
        except IconValidationError as err:
            self._diagnostic(operatorId, err.code, str(err))
        binding = _Binding(self._serial, weakref.ref(target), id(target), operatorId, context, mode, size, priority, request)
        self._bindings[binding.token] = binding
        self._targets[id(target)] = binding.token
        self._apply(binding, operatorIcon(str(definition.get("iconKey", "default"))), "fallback", "")
        if request:
            self._ensure(binding, allowRender=False)
        return binding.token

    def unsubscribe(self, token: int) -> None:
        binding = self._bindings.pop(token, None)
        if binding:
            if self._targets.get(binding.targetId) == token:
                self._targets.pop(binding.targetId, None)
            if binding.request and not any(b.request == binding.request for b in self._bindings.values()):
                self.worker.cancel(binding.request)

    def unbind(self, target) -> None:
        token = self._targets.get(id(target))
        if token is not None:
            self.unsubscribe(token)

    def _apply(self, binding: _Binding, icon, source: str, sha: str) -> None:
        target = binding.target()
        if (self._closed or not self._valid(target) or self._bindings.get(binding.token) is not binding
                or self._targets.get(id(target)) != binding.token
                or self.scope != str(getattr(self.runtimeClient, "runtimeScope", id(self.runtimeClient)))
                or (binding.request and (binding.request.scope != self.scope or binding.request.generation != self.generation))):
            return
        if binding.mode == "label":
            target.setPixmap(icon.pixmap(binding.size, binding.size))
        elif binding.mode == "window":
            target.setWindowIcon(icon)
        elif binding.mode == "list":
            target.setIcon(icon)
        else:
            target.setOperatorIcon(icon)
        target._operatorIconSource = source
        target._operatorIconSha = sha

    def _ensure(self, binding: _Binding, *, allowRender: bool = True) -> None:
        request = binding.request
        if request is None:
            return
        target = binding.target()
        if not self._valid(target):
            self.unsubscribe(binding.token)
            return
        failure = self._failures.get(request.resourceKey)
        if failure and failure.code not in {"E_DISPLAY_TIMEOUT", "E_DISPLAY_FAILED"} and "UNAVAILABLE" not in failure.code and "DEADLINE" not in failure.code:
            if failure.generation == self.generation:
                return
        elif failure and (failure.attempts >= 2 or time.monotonic() < failure.retryAt):
            return
        asset = self.cache.get(request)
        if asset is None:
            if not self._unsupported:
                self.worker.request(request, priority=binding.priority)
            return
        dprMethod = getattr(target, "devicePixelRatioF", None)
        dpr = float(dprMethod()) if callable(dprMethod) else float(QCoreApplication.instance().devicePixelRatio())
        key = (self.scope, request.mimeType, request.sha256, binding.size, dpr)
        if binding.applied == key:
            return
        rendered = self._rendered.get(key)
        if rendered is None and not allowRender:
            return
        try:
            if rendered is None:
                rendered = renderIconAsset(asset, binding.size, dpr)
                self._rendered[key] = rendered
                self._renderedBytes += rendered.pixelSize ** 2 * 4
                while len(self._rendered) > 256 or self._renderedBytes > 32 * 1024 * 1024:
                    _, removed = self._rendered.popitem(last=False)
                    self._renderedBytes -= removed.pixelSize ** 2 * 4
            else:
                self._rendered.move_to_end(key)
            self._apply(binding, rendered.icon, "custom", rendered.sha256)
            binding.applied = key
        except Exception as err:
            self._recordFailure(request, "E_ICON_DECODE_FAILED", str(err))

    def _diagnostic(self, operatorId: str, code: str, message: str, digest: str = "") -> None:
        key = self.scope, operatorId, code, digest
        if key not in self._logged and self._appendLog:
            self._appendLog("WARNING", f"Icon {operatorId}: {code}: {message}")
        self._logged.add(key)

    def _recordFailure(self, request: IconRequest, code: str, message: str) -> None:
        previous = self._failures.get(request.resourceKey)
        self._failures[request.resourceKey] = _Failure(code, message, (previous.attempts if previous else 0) + 1, time.monotonic() + 5, self.generation)
        self._diagnostic(request.operatorId, code, message, request.sha256)
        if "UNIMPLEMENTED" in code:
            self._unsupported = True
        if code in {"E_ICON_VERSION_MISMATCH", "E_ICON_DIGEST_MISMATCH"} and not self._recoveryUsed:
            self._recoveryUsed = True
            if self._onRecovery:
                self._onRecovery()

    def poll(self) -> None:
        if self._closed:
            return
        for result in self.worker.poll():
            request = result.key
            if not isinstance(request, IconRequest) or request.scope != self.scope:
                continue
            if result.code:
                if request.generation == self.generation:
                    self._recordFailure(request, result.code, result.message)
            else:
                self.cache.put(request.scope, result.payload)
                self._failures.pop(request.resourceKey, None)
        # GUI work is bounded per event-loop turn even for very large canvases.
        pending = list(self._bindings.values())
        offset = getattr(self, "_cursor", 0) % max(1, len(pending))
        for binding in (pending[offset:] + pending[:offset])[:8]:
            if not self._valid(binding.target()):
                self.unsubscribe(binding.token)
            else:
                self._ensure(binding)
        self._cursor = offset + 8

    def close(self, catalogWorker=None, timeout: float = 3.0) -> bool:
        if self._closed:
            return not any(worker.isRunning() for worker in self._closingWorkers)
        self._closed = True
        self._timer.stop()
        self._bindings.clear()
        self._targets.clear()
        workers = [self.worker] + ([catalogWorker] if catalogWorker is not None else [])
        self._closingWorkers = workers
        for worker in workers:
            worker.beginClose()
        end = time.monotonic() + timeout
        while any(worker.isRunning() for worker in workers) and time.monotonic() < end:
            time.sleep(0.002)
            if any(worker.isRunning() for worker in workers):
                QCoreApplication.processEvents(QEventLoop.ExcludeUserInputEvents, 10)
        complete = not any(worker.isRunning() for worker in workers)
        if not complete:
            self._diagnostic("", "E_DISPLAY_SHUTDOWN_TIMEOUT", "read-only workers exceeded the shared shutdown budget")
        self._rendered.clear()
        self._renderedBytes = 0
        self.cache.clear()
        return complete
