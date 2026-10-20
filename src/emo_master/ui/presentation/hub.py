"""One GUI timer and image cache for all windows sharing one DisplaySession."""
from collections import OrderedDict
from uuid import uuid4
import weakref

from shiboken2 import isValid

from PySide2.QtCore import QObject, QTimer

from emo_master.ui.presentation.images import assertGuiThread, ownedImage


class DisplayHub(QObject):
    imageLimit = 24 * 1024 * 1024

    def __init__(self, session, parent=None):
        super().__init__(parent)
        assertGuiThread()
        self.session = session
        self.windows = set()
        self._windowLinks = {}
        self.imageConsumer = uuid4().hex
        self.cache = OrderedDict()
        self.cacheBytes = 0
        self.conversions = 0
        self.coalesced = 0
        self.lastToken = None
        self.lastOrdinal = {}
        self.pins = {}
        self.lifecycle = {"retired": False}
        # QObject destruction can precede a child window's closeEvent. Cleanup
        # captures only the Qt-free session/token/lease map, never a dead hub.
        def releaseOwnedState(_object=None, session=session, consumer=self.imageConsumer, pins=self.pins,
                              lifecycle=self.lifecycle, links=self._windowLinks):
            lifecycle["retired"] = True
            for link in links.values():
                link["owner"] = None
            links.clear()
            if getattr(session, 'imageDemand', False):
                session.removeImageDemand(consumer)
            for ticket in tuple(pins.values()):
                session.pins().release(ticket)
            pins.clear()
        self.destroyed.connect(releaseOwnedState)
        self.timer = QTimer(self)
        self.timer.setInterval(16)
        self.timer.timeout.connect(self.tick)

    def attach(self, window):
        assertGuiThread()
        if window in self.windows:
            return
        if len(self.windows) >= 2:
            raise ValueError("最多两个共享窗口")
        link = getattr(window, '_displayHubRetirement', None)
        if link is not None and link['owner'] is not None and link['owner']() is not self:
            previous = link['owner']()
            if previous is not None:
                previous.detach(window)
        if link is None:
            # One hook for the QWidget's entire lifetime. Reattachment changes
            # only a weak owner token; retiring a hub needs no native Qt calls.
            link = {'owner': None}
            window._displayHubRetirement = link
            windowRef = weakref.ref(window)
            def retired(_object=None, link=link, windowRef=windowRef):
                owner = link['owner']
                link['owner'] = None
                hub, item = owner() if owner is not None else None, windowRef()
                if hub is not None and item is not None:
                    hub.detach(item)
            window.destroyed.connect(retired)
        link['owner'] = weakref.ref(self)
        self._windowLinks[window] = link
        window.setSimulationState(None)
        self.windows.add(window)
        self.updateImageDemand()
        self.timer.start()
        window.submit(self.session.readSnapshot())

    def detach(self, window):
        assertGuiThread()
        link = self._windowLinks.pop(window, None)
        if link is not None:
            link['owner'] = None
        self.windows.discard(window)
        self.resume(window)
        if not self.windows:
            if not self.lifecycle["retired"] and isValid(self.timer):
                self.timer.stop()
            self.cache.clear()
            self.cacheBytes = 0
        # Application owner closes the borrowed session off the GUI thread.

    def updateImageDemand(self):
        assertGuiThread()
        if not getattr(self.session, 'imageDemand', False):
            return
        if self.lifecycle["retired"] or not self.windows:
            self.session.removeImageDemand(self.imageConsumer)
            return
        sources = set()
        for window in self.windows:
            visible = window.isVisible() and not window.detached
            if window in self.pins:
                self.session.pins().setActive(self.pins[window], visible)
            elif visible:
                sources.update(window.imageSources())
        self.session.setImageDemand(self.imageConsumer, sources)

    def image(self, scope, sourceId, pixels):
        # Aliases share the same validated immutable asset; charge/convert it
        # once rather than once per binding. Distinct assets remain distinct.
        resultKey = scope.result.identity.resultKey
        source = next((item for item in scope.result.sources if item.sourceId == sourceId), None)
        asset = source.image if source is not None else None
        key = (resultKey, 'asset', asset.resourceId, asset.sha256, asset.byteSize) if asset is not None else (resultKey, 'source', sourceId)
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

    def freeze(self, window, scope, generation, ttlMs=30000, *, sourceIds=None):
        if window in self.pins:
            raise ValueError("请先恢复实时，再锁定其他结果")
        ticket = self.session.pins().acquire(scope, generation, ttlMs, sourceIds=sourceIds)
        self.pins[window] = ticket
        self.updateImageDemand()
        self.lastToken = None
        return ticket

    def resume(self, window):
        ticket = self.pins.pop(window, None)
        if ticket:
            self.session.pins().release(ticket)
        self.updateImageDemand()
        self.lastToken = None

    def tick(self):
        assertGuiThread()
        if self.lifecycle["retired"]:
            return
        self.updateImageDemand()
        view = self.session.readSnapshot()
        token = (view.generation, view.connection, view.detail, getattr(view, 'job', None),
                 tuple((s, r.result.identity.resultKey, r.readyNs, tuple(r.imageStates.items())) for s, r in view.scopes.items()),
                 tuple((s, r.identity.resultKey) for s, r in view.loading.items()), tuple(view.started.items()),
                 tuple(getattr(view, 'expiredScopes', {}).items()),
                 tuple((ticket, pin.state, pin.scope.readyNs if pin.scope else 0)
                       for ticket in self.pins.values() for pin in (self.session.pins().read(ticket),)))
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
                "window_surface_estimated_bytes": sum(window.surfaceBytes() for window in self.windows),
                "table_bytes": sum(w.model.bytesHeld for window in self.windows for rows in window.widgets.values()
                                   for _c, w in rows.values() if isinstance(w, CollectionView)),
                "table_limit": len(self.windows) * 8 * 1024 * 1024}
