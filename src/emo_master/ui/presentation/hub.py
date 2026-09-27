"""One GUI timer and image cache for all windows sharing one DisplaySession."""
from collections import OrderedDict

from PySide2.QtCore import QObject, QTimer

from emo_master.ui.presentation.images import assertGuiThread, ownedImage


class DisplayHub(QObject):
    imageLimit = 24 * 1024 * 1024

    def __init__(self, session, parent=None):
        super().__init__(parent)
        assertGuiThread()
        self.session = session
        self.windows = set()
        self.cache = OrderedDict()
        self.cacheBytes = 0
        self.conversions = 0
        self.coalesced = 0
        self.lastToken = None
        self.lastOrdinal = {}
        self.timer = QTimer(self)
        self.timer.setInterval(16)
        self.timer.timeout.connect(self.tick)

    def attach(self, window):
        assertGuiThread()
        if len(self.windows) >= 2:
            raise ValueError("最多两个共享窗口")
        self.windows.add(window)
        self.timer.start()
        window.submit(self.session.readSnapshot())

    def detach(self, window):
        assertGuiThread()
        self.windows.discard(window)
        if not self.windows:
            self.timer.stop()
            self.cache.clear()
            self.cacheBytes = 0
        # Application owner closes the borrowed session off the GUI thread.

    def image(self, scope, sourceId, pixels):
        key = (scope.result.identity.resultKey, sourceId)
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]
        channels = pixels.shape[2] if pixels.ndim == 3 else 1
        size = ((pixels.shape[1] * channels + 3) // 4 * 4) * pixels.shape[0]
        if size > 8 * 1024 * 1024 or self.cacheBytes + size > self.imageLimit:
            raise ValueError("UI_IMAGE_BUDGET")
        image = ownedImage(pixels)
        self.cache[key] = image
        self.cacheBytes += image.sizeInBytes()
        self.conversions += 1
        return image

    def tick(self):
        assertGuiThread()
        view = self.session.readSnapshot()
        token = (view.generation, view.connection, view.detail,
                 tuple((s, r.result.identity.resultKey) for s, r in view.scopes.items()),
                 tuple((s, r.identity.resultKey) for s, r in view.loading.items()), tuple(view.started.items()))
        if token == self.lastToken:
            return
        self.lastToken = token
        keep = {r.result.identity.resultKey for r in view.scopes.values()}
        for key in list(self.cache):
            if key[0] not in keep:
                self.cacheBytes -= self.cache.pop(key).sizeInBytes()
        for scope, item in view.scopes.items():
            address = (view.generation, scope)
            ordinal = item.result.identity.resultOrdinal
            previous = self.lastOrdinal.get(address, ordinal - 1)
            self.coalesced += max(0, ordinal - previous - 1)
            self.lastOrdinal[address] = ordinal
        self.lastOrdinal = {k: v for k, v in self.lastOrdinal.items() if k[0] == view.generation}
        for window in tuple(self.windows):
            if window.isVisible():
                window.submit(view)

    def stats(self):
        return {"windows": len(self.windows), "qt_image_bytes": self.cacheBytes,
                "qt_image_limit": self.imageLimit, "conversions": self.conversions,
                "coalesced_results": self.coalesced, "pending_gui_notifications": 0}
