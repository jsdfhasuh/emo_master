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


def testCanvasViewWheelZoomChangesScale() -> None:
    ensureQApp()
    window = MainWindow(RuntimeClientStub())
    getZoomFactor = getattr(window.flowView, "getZoomFactor", None)
    zoomByDelta = getattr(window.flowView, "zoomByDelta", None)
    assert callable(getZoomFactor)
    assert callable(zoomByDelta)

    initial = getZoomFactor()
    zoomByDelta(120)
    updated = getZoomFactor()
    assert updated > initial


def testCanvasViewBlankDragStartsPanMode() -> None:
    ensureQApp()
    window = MainWindow(RuntimeClientStub())
    beginPanAt = getattr(window.flowView, "beginPanAt", None)
    isPanning = getattr(window.flowView, "isPanning", None)
    endPan = getattr(window.flowView, "endPan", None)
    assert callable(beginPanAt)
    assert callable(isPanning)
    assert callable(endPan)

    assert beginPanAt(9999.0, 9999.0) is True
    assert isPanning() is True
    endPan()
    assert isPanning() is False


def testCanvasViewNodeRegionDoesNotStartPanMode() -> None:
    ensureQApp()
    window = MainWindow(RuntimeClientStub())
    window.addNodeFromOperatorPayload(
        {
            "operatorId": "vision.io.image_loader",
            "displayName": "Image Loader",
            "inputPorts": {},
            "outputPorts": {"image": "image"},
            "paramSchema": {},
        },
        sceneX=60.0,
        sceneY=80.0,
    )
    beginPanAt = getattr(window.flowView, "beginPanAt", None)
    assert callable(beginPanAt)
    assert beginPanAt(80.0, 100.0) is False


def testMainWindowUsesDesignerGraphicsView() -> None:
    ensureQApp()
    window = MainWindow(RuntimeClientStub())
    getZoomFactor = getattr(window.flowView, "getZoomFactor", None)
    beginPanAt = getattr(window.flowView, "beginPanAt", None)
    assert callable(getZoomFactor)
    assert callable(beginPanAt)


def testCanvasViewHidesScrollBars() -> None:
    ensureQApp()
    window = MainWindow(RuntimeClientStub())
    getScrollBarVisibility = getattr(window.flowView, "getScrollBarVisibility", None)
    assert callable(getScrollBarVisibility)
    visibility = getScrollBarVisibility()
    assert visibility == {"horizontal": "off", "vertical": "off"}
