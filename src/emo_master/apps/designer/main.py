import os
from pathlib import Path
from typing import cast

import grpc

from emo_master.apps.designer.services.runtime_client import (
    RuntimeClient,
    RuntimeServiceProtocol,
)
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2_grpc
from emo_master.apps.runtime.grpc_server.service import RuntimeService


def resolveRuntimeTarget() -> str:
    return os.getenv("EMO_RUNTIME_TARGET", "").strip()


def applyDesignerStyle(app) -> None:
    from emo_master.apps.designer.ui.theme import configureTheme

    configureTheme(app)
    stylePath = Path(__file__).resolve().parent / "ui" / "styles" / "app.qss"
    if not stylePath.exists():
        return
    styleText = stylePath.read_text(encoding="utf-8")
    app.setStyleSheet(styleText)


def _configureHighDpi(qCoreApplication, qt, qGuiApplication) -> bool:
    instance = getattr(qCoreApplication, "instance", None)
    if callable(instance) and instance() is not None:
        return False

    configured = False
    setAttribute = getattr(qCoreApplication, "setAttribute", None)
    if callable(setAttribute):
        for attributeName in ["AA_EnableHighDpiScaling", "AA_UseHighDpiPixmaps"]:
            attribute = getattr(qt, attributeName, None)
            if attribute is None:
                continue
            setAttribute(attribute, True)
            configured = True

    policyType = getattr(qt, "HighDpiScaleFactorRoundingPolicy", None)
    passThrough = getattr(policyType, "PassThrough", None)
    setRoundingPolicy = getattr(
        qGuiApplication, "setHighDpiScaleFactorRoundingPolicy", None
    )
    if callable(setRoundingPolicy) and passThrough is not None:
        setRoundingPolicy(passThrough)
        configured = True
    return configured


def configureHighDpi() -> bool:
    from PySide2.QtCore import QCoreApplication, Qt
    from PySide2.QtGui import QGuiApplication

    return _configureHighDpi(QCoreApplication, Qt, QGuiApplication)


def runDesigner() -> None:
    configureHighDpi()
    from PySide2.QtWidgets import QApplication

    from emo_master.apps.designer.ui.main_window import MainWindow

    app = QApplication([])
    applyDesignerStyle(app)
    runtimeTarget = resolveRuntimeTarget()
    runtimeClient: RuntimeClient | None = None
    window = None
    try:
        if runtimeTarget != "":
            channel = grpc.insecure_channel(runtimeTarget)
            runtimeService = cast(
                RuntimeServiceProtocol, runtime_pb2_grpc.RuntimeServiceStub(channel)
            )
            runtimeClient = RuntimeClient(
                runtimeService=runtimeService,
                ownedChannel=channel,
            )
        else:
            embeddedService = RuntimeService()
            runtimeService = cast(RuntimeServiceProtocol, embeddedService)
            runtimeClient = RuntimeClient(
                runtimeService=runtimeService,
                ownedRuntimeService=embeddedService,
            )
        window = MainWindow(runtimeClient, showStartupEntry=True)
        shouldShow = window.showStartupProjectEntry()
        if not shouldShow:
            return
        window.show()
        app.exec_()
    finally:
        if window is not None:
            window.shutdownOperatorDisplay()
        if runtimeClient is not None:
            runtimeClient.close()


if __name__ == "__main__":
    runDesigner()
