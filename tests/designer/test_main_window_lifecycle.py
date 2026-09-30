"""GUI-owned scene lifetime must not depend on Python cyclic GC's thread."""
import gc
import threading

import emo_master  # noqa: F401 - preload Windows dependency DLLs before Qt
import shiboken2
from PySide2.QtCore import QCoreApplication, QEvent

from emo_master.apps.designer.ui.main_window import MainWindow


class Client:
    def listOperators(self):
        return []


def testWindowOwnsSceneAndDestroysItOnGuiThreadBeforeWorkerGc():
    window = MainWindow(Client())
    scene = window.flowScene
    destroyed = []
    scene.destroyed.connect(lambda *_: destroyed.append(threading.get_ident()))
    assert scene.parent() is window
    guiThread = threading.get_ident()
    assert window.close()
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    # Keep the Python scene reference: its native lifetime belongs to the
    # window, not a later cyclic collector running in a catalog/RPC worker.
    assert not shiboken2.isValid(scene)
    assert destroyed == [guiThread]
    worker = threading.Thread(target=gc.collect, name='scene-gc-regression')
    worker.start()
    worker.join(timeout=3)
    assert not worker.is_alive()
    assert destroyed == [guiThread]
