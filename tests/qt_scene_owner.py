"""Explicit test ownership for standalone FlowScenes, independent of cyclic GC."""
import threading


class OwnedFlowScenes:
    def __init__(self, application):
        self.application = application
        self.guiThread = threading.get_ident()
        self.scenes = []

    def _assertGuiThread(self):
        if threading.get_ident() != self.guiThread:
            raise RuntimeError('test scenes must be created and retired on the GUI thread')
        if self.application is not None:
            from PySide2.QtCore import QThread
            if QThread.currentThread() != self.application.thread():
                raise RuntimeError('test scene owner must run on the QApplication thread')

    def __call__(self):
        from emo_master.apps.designer.ui.flow_scene import FlowScene
        self._assertGuiThread()
        scene = FlowScene()
        self.scenes.append(scene)
        return scene

    def retire(self):
        self._assertGuiThread()
        if self.application is None:
            for scene in self.scenes:
                scene.clearGraph()
            self.scenes.clear()
            return
        from PySide2.QtCore import QCoreApplication, QEvent
        import shiboken2
        # Hold wrappers until the C++ scene/items have actually been destroyed.
        # DeferredDelete is targeted, so teardown does not pump unrelated work.
        for scene in self.scenes:
            if shiboken2.isValid(scene):
                try:
                    scene.endDragPreview()
                    scene.clearGraph()
                finally:
                    scene.deleteLater()
                    QCoreApplication.sendPostedEvents(scene, QEvent.DeferredDelete)
                assert not shiboken2.isValid(scene)
        self.scenes.clear()
