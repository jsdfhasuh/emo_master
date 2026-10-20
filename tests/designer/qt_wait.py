import time


def waitUntil(predicate, timeout=3.0, *, pump=True):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError("condition did not become true before timeout")
        if pump:
            from PySide2.QtCore import QCoreApplication
            QCoreApplication.processEvents()
        time.sleep(0.003)


def waitForCatalog(window):
    waitUntil(lambda: window.operatorCatalogController.hasCatalog)
