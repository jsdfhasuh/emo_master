from emo_master.apps.designer.ui.main_window import MainWindow


def ensureQApp() -> None:
    try:
        from PySide2.QtWidgets import QApplication

        app = QApplication.instance()
        if app is None:
            _ = QApplication([])
    except Exception:
        pass


class RuntimeClientStub:
    def listOperators(self):
        return []


def testMainWindowUsesLargeFixedCanvasArea() -> None:
    ensureQApp()
    window = MainWindow(RuntimeClientStub())
    getSceneRect = getattr(window.flowScene, "sceneRect", None)
    if callable(getSceneRect):
        rect = getSceneRect()
        width = float(getattr(rect, "width", lambda: 0.0)())
        height = float(getattr(rect, "height", lambda: 0.0)())
        assert width >= 4000.0
        assert height >= 4000.0
        return

    rawRect = getattr(window.flowScene, "_sceneRect", None)
    assert isinstance(rawRect, tuple)
    assert len(rawRect) == 4
    assert float(rawRect[2]) >= 4000.0
    assert float(rawRect[3]) >= 4000.0
