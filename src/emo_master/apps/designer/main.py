import os
from pathlib import Path
from typing import cast

import grpc

from emo_master.apps.designer.services.runtime_client import (
    RuntimeClient,
    RuntimeServiceProtocol,
)
from emo_master.apps.designer.ui.main_window import MainWindow
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2_grpc
from emo_master.apps.runtime.grpc_server.service import RuntimeService


def resolveRuntimeTarget() -> str:
    return os.getenv("EMO_RUNTIME_TARGET", "").strip()


def applyDesignerStyle(app) -> None:
    stylePath = Path(__file__).resolve().parent / "ui" / "styles" / "app.qss"
    if not stylePath.exists():
        return
    styleText = stylePath.read_text(encoding="utf-8")
    app.setStyleSheet(styleText)


def runDesigner() -> None:
    from PySide2.QtWidgets import QApplication

    app = QApplication([])
    applyDesignerStyle(app)
    runtimeTarget = resolveRuntimeTarget()
    runtimeClient: RuntimeClient | None = None
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
        if runtimeClient is not None:
            runtimeClient.close()


if __name__ == "__main__":
    runDesigner()
