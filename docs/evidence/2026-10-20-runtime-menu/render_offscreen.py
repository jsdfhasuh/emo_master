"""Reproduce layout evidence with synthetic input and an empty local Runtime.

Run from the repository root with PYTHONPATH=src;. and QT_QPA_PLATFORM=offscreen.
This is not a desktop/DPI, frozen-product, or physical-device acceptance script.
"""
import json
import os
from pathlib import Path
import tempfile
import time

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
import emo_master  # noqa: E402, F401 - Windows native dependency bootstrap before Qt
from PySide2.QtCore import QSettings  # noqa: E402
from PySide2.QtGui import QFontDatabase  # noqa: E402
from PySide2.QtWidgets import QApplication, QMessageBox  # noqa: E402

from emo_master.apps.designer.main import applyDesignerStyle  # noqa: E402
from emo_master.apps.designer.services.runtime_client import RuntimeClient  # noqa: E402
from emo_master.apps.designer.state.project_store import saveProject  # noqa: E402
from emo_master.apps.designer.ui.project_configuration import ProjectConfigurationDialog  # noqa: E402
from emo_master.apps.runtime.grpc_server.service import RuntimeService  # noqa: E402
from tests.qt_widget_owner import OwnedDesignerWindows  # noqa: E402
from tests.runtime.test_device_runtime import simulatedProject  # noqa: E402
from tests.runtime.test_initial_project_configuration import imageProject  # noqa: E402


def main():
    output = Path(__file__).resolve().parent
    temporary = Path(tempfile.mkdtemp(prefix='emo-runtime-menu-render-'))
    os.environ['EMO_RUNTIME_DATA_DIR'] = str(temporary / 'runtime-data')
    application = QApplication.instance() or QApplication([])
    # The Windows offscreen font database does not enumerate desktop fonts.
    assert QFontDatabase.addApplicationFont('C:/Windows/Fonts/msyh.ttc') >= 0
    applyDesignerStyle(application)
    QMessageBox.question = lambda *_args, **_kwargs: QMessageBox.Discard

    def settle(predicate=lambda: True):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            application.processEvents()
            if predicate():
                for _ in range(6):
                    application.processEvents()
                return
            time.sleep(.01)
        raise AssertionError('offscreen evidence did not settle')

    captures = {}
    def capture(widget, name, size=None):
        widget.show()
        settle()
        if size:
            widget.resize(*size)
        settle()
        assert widget.grab().save(str(output / (name + '.png')))
        captures[name] = dict(width=widget.width(), height=widget.height(), dpr=widget.devicePixelRatioF())

    service = RuntimeService(dbPath=temporary / 'data/runtime.db', workspaceRoot=temporary / 'data/jobs')
    client = RuntimeClient(service)
    try:
        with OwnedDesignerWindows(application) as owner:
            window = owner(client, settingsStore=QSettings(str(temporary / 'settings.ini'), QSettings.IniFormat))
            root = temporary / 'engineering'
            payload = imageProject(root)
            payload['devices'] = simulatedProject()['devices']
            payload['project']['name'] = '图片流程与设备草稿（隔离示例）'
            path = root / 'example.emoproj'
            saveProject(path, payload)
            assert window.loadProjectDirectory(str(path), confirmed=True)
            settle(lambda: window.operatorCatalogController.state == 'ready')
            window.autoLayoutNodes()
            capture(window, 'main-1280', (1280, 720))
            capture(window, 'main-642', (642, 452))
            window.pageCoordinator.chrome.menus['Runtime'].popup(window.menuBar().mapToGlobal(window.menuBar().rect().bottomLeft()))
            capture(window.pageCoordinator.chrome.menus['Runtime'], 'runtime-menu')
            window.pageCoordinator.chrome.menus['Runtime'].close()
            window.pageCoordinator.chrome.deviceAction.trigger()
            devices = window.pageCoordinator.devices
            settle(lambda: devices.pending is None and bool(devices.stateNotice))
            devices.table.setCurrentCell(0, 0)
            capture(devices, 'devices-1200', (1200, 760))
            capture(devices, 'devices-642', (642, 452))
            devices.closeButton.click()
            configuration = ProjectConfigurationDialog(window.pageCoordinator, window)
            settle(lambda: configuration.pending is None)
            capture(configuration, 'first-write', (1050, 650))
            configuration.reject()
            monitor = window.pageCoordinator.showOnline()
            settle(lambda: monitor.pending is None)
            capture(monitor, 'monitor-empty', (1100, 720))
            monitor.close()
            assert service.loadedDocument is None and not service.jobRepository.all() and not service.deviceManager.drivers
    finally:
        client.close()
        service.close()
    (output / 'manifest.json').write_text(json.dumps(dict(platform=application.platformName(),
        temporaryWorkspace=str(temporary), physicalDevices=False, jobsStarted=0, captures=captures),
        ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
