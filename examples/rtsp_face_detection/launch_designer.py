"""Open this bundle in the source Designer with isolated local Runtime data."""
import os
from pathlib import Path


def main():
    root = Path(__file__).resolve().parent
    data = root / "runtime-data"
    data.mkdir(exist_ok=True)
    os.environ["EMO_RUNTIME_DATA_DIR"] = str(data)
    os.environ.pop("EMO_RUNTIME_DB_PATH", None)
    from emo_master.apps.designer.main import applyDesignerStyle, configureHighDpi
    configureHighDpi()
    from PySide2.QtCore import QSettings
    from PySide2.QtWidgets import QApplication
    from emo_master.apps.designer.services.runtime_client import RuntimeClient
    from emo_master.apps.designer.ui.main_window import MainWindow
    from emo_master.apps.runtime.grpc_server.service import RuntimeService

    app = QApplication([])
    applyDesignerStyle(app)
    service = RuntimeService()
    client = RuntimeClient(runtimeService=service, ownedRuntimeService=service)
    window = None
    try:
        window = MainWindow(client, settingsStore=QSettings(str(data / "designer.ini"), QSettings.IniFormat))
        if not window.loadProjectSelection(str(root / "rtsp-face-detection.emoproj")):
            raise RuntimeError("Designer rejected the project; see its error dialog")
        window.show()
        app.exec_()
    finally:
        if window is not None:
            window.shutdownOperatorDisplay()
        client.close()


if __name__ == "__main__":
    main()
