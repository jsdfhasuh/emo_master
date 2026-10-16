"""Shared Qt test ownership for top-level windows and nested owned popups."""

import threading


def createdWidgetRoots(created):
    """Retire a created popup through any created QWidget ancestor."""
    createdSet = set(created)
    roots = []
    for widget in created:
        parent = widget.parentWidget()
        while parent is not None and parent not in createdSet:
            parent = parent.parentWidget()
        if parent is None:
            roots.append(widget)
    return roots


class OwnedDesignerWindows:
    """Retain and retire only the native Designer roots created by a test."""

    def __init__(self, application):
        self.application = application
        self.guiThread = threading.get_ident()
        self._owned = []

    def _assertGuiThread(self):
        from PySide2.QtCore import QThread
        if (self.application is None or threading.get_ident() != self.guiThread
                or QThread.currentThread() != self.application.thread()):
            raise RuntimeError('Designer test windows must be created and retired on the QApplication thread')

    def __call__(self, runtimeClient, **kwargs):
        from emo_master.apps.designer.ui.main_window import MainWindow
        self._assertGuiThread()
        window = MainWindow(runtimeClient, **kwargs)
        # Keep the complete native ownership chain alive until GUI retirement.
        self._owned.append((window, window.flowScene, window.operatorBubble))
        return window

    def owns(self, window):
        return any(owned[0] is window for owned in self._owned)

    def __enter__(self):
        return self

    def __exit__(self, _kind, _value, _traceback):
        self.retire()

    def retire(self):
        from PySide2.QtCore import QCoreApplication, QEvent
        from shiboken2 import isValid
        self._assertGuiThread()
        errors = []
        accepted = []
        # close() can pump Qt events. Close every root before deleting any root.
        for index, (window, _scene, _bubble) in enumerate(self._owned):
            if not isValid(window):
                accepted.append(True)
                continue
            try:
                closed = window.close()
                accepted.append(bool(closed))
                if not closed:
                    errors.append(f'window {index} refused close')
            except Exception as error:
                accepted.append(False)
                errors.append(f'window {index} close failed: {error}')

        safe = []
        retained = []
        for index, (owned, closed) in enumerate(zip(self._owned, accepted)):
            window, _scene, _bubble = owned
            ready = closed
            if isValid(window):
                if not window.runtimeController._closed:
                    errors.append(f'window {index} runtime controller is not closed')
                    ready = False
                provider = window.operatorIconProvider
                workers = [('icon', provider.worker if provider is not None else None),
                           ('catalog', window.operatorCatalogWorker)]
                for name, worker in workers:
                    if worker is not None and worker.isRunning():
                        errors.append(f'window {index} {name} workers are still running')
                        ready = False
            (safe if ready else retained).append(owned)

        for window, _scene, _bubble in safe:
            if isValid(window):
                window.deleteLater()
        for window, _scene, _bubble in safe:
            if isValid(window):
                QCoreApplication.sendPostedEvents(window, QEvent.DeferredDelete)
        for index, owned in enumerate(safe):
            remaining = [name for name, obj in zip(('window', 'scene', 'bubble'), owned)
                         if isValid(obj)]
            if remaining:
                errors.append(f'retired window {index} still has native objects: {remaining}')
                retained.append(owned)
        self._owned = retained
        if errors:
            raise AssertionError('; '.join(errors))
