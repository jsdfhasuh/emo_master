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
        self.pins = {}
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
        self.resume(window)
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
        if size > 8 * 1024 * 1024 or self.imageBytes() + size > self.imageLimit:
            raise ValueError("UI_IMAGE_BUDGET")
        image = ownedImage(pixels)
        self.cache[key] = image
        self.cacheBytes += image.sizeInBytes()
        self.conversions += 1
        return image

    def imageBytes(self):
        images = {image.cacheKey(): image.sizeInBytes() for image in self.cache.values()}
        from emo_master.ui.presentation.renderer import ImageView
        for window in self.windows:
            for rows in window.widgets.values():
                for _component, widget in rows.values():
                    if isinstance(widget, ImageView) and not widget.image.isNull():
                        images[widget.image.cacheKey()] = widget.image.sizeInBytes()
        return sum(images.values())

    def freeze(self, window, scope, generation, ttlMs=30000):
        if window in self.pins:
            raise ValueError("请先恢复实时，再锁定其他结果")
        ticket = self.session.pins().acquire(scope, generation, ttlMs)
        self.pins[window] = ticket
        self.lastToken = None
        return ticket

    def resume(self, window):
        ticket = self.pins.pop(window, None)
        if ticket:
            self.session.pins().release(ticket)
        self.lastToken = None

    def tick(self):
        assertGuiThread()
        view = self.session.readSnapshot()
        token = (view.generation, view.connection, view.detail,
                 tuple((s, r.result.identity.resultKey) for s, r in view.scopes.items()),
                 tuple((s, r.identity.resultKey) for s, r in view.loading.items()), tuple(view.started.items()),
                 tuple((ticket, self.session.pins().read(ticket).state) for ticket in self.pins.values()))
        if token == self.lastToken:
            return
        self.lastToken = token
        keep = {r.result.identity.resultKey for r in view.scopes.values()}
        for ticket in self.pins.values():
            pin = self.session.pins().read(ticket)
            if pin.scope:
                keep.add(pin.scope.result.identity.resultKey)
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
        snapshot = self.session.readSnapshot()
        from emo_master.ui.presentation.table import CollectionView
        live = {id(image): image.nbytes for scope in snapshot.scopes.values() for image in scope.images.values()}
        retained = {}
        for window in self.windows:
            scopes = list(window.displayed.values())
            if window.lastView:
                scopes.extend(window.lastView.scopes.values())
            for scope in scopes:
                retained.update({id(image): image.nbytes for image in scope.images.values()})
        pins = self.session._pinStore.bytesHeld() if self.session._pinStore else 0
        return {"windows": len(self.windows), "qt_image_bytes": self.imageBytes(),
                "qt_image_limit": self.imageLimit, "conversions": self.conversions,
                "coalesced_results": self.coalesced, "pending_gui_notifications": 0,
                "live_decoded_bytes": sum(live.values()),
                "ui_decoded_reference_bytes": sum(retained.values()),
                "live_ui_decoded_bytes": sum({**live, **retained}.values()),
                # Conservative: pins may overlap visible references; include
                # canceled pins until their worker has actually released them.
                "decoded_retention_accounted_bytes": sum({**live, **retained}.values()) + pins,
                "decoded_retention_reserved": (16 + 16 * len(self.windows) + 16) * 1024 * 1024,
                "pin_decoded_bytes": pins,
                "pin_limit": 16 * 1024 * 1024, "conversion_scratch_limit": 8 * 1024 * 1024,
                "window_surface_reserved": len(self.windows) * 16 * 1024 * 1024,
                "table_bytes": sum(w.model.bytesHeld for window in self.windows for rows in window.widgets.values()
                                   for _c, w in rows.values() if isinstance(w, CollectionView)),
                "table_limit": len(self.windows) * 8 * 1024 * 1024}
