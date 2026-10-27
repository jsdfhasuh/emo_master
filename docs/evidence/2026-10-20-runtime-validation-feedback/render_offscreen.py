"""Synthetic Qt renders, not native desktop or hardware acceptance."""
import json
import os
from pathlib import Path
import tempfile

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
import emo_master  # noqa: E402, F401 - initialize Windows native dependency paths
from PySide2.QtCore import QSettings  # noqa: E402
from PySide2.QtGui import QFontDatabase  # noqa: E402
from PySide2.QtWidgets import QApplication, QMessageBox, QDialogButtonBox  # noqa: E402
from shiboken2 import isValid  # noqa: E402

from emo_master.apps.designer.main import applyDesignerStyle  # noqa: E402
from emo_master.apps.designer.services.runtime_client import RuntimeClient, RuntimeClientError  # noqa: E402
from emo_master.apps.designer.ui import runtime_connection  # noqa: E402
from emo_master.apps.runtime.grpc_server.service import RuntimeService  # noqa: E402
from tests.designer.test_device_workspace import waitUntil  # noqa: E402
from tests.designer.test_runtime_validation_feedback import legacyDialog  # noqa: E402
from tests.qt_widget_owner import OwnedDesignerWindows  # noqa: E402


def main():
    output = Path(__file__).resolve().parent
    temporary = Path(tempfile.mkdtemp(prefix='emo-runtime-feedback-render-'))
    os.environ['EMO_RUNTIME_DATA_DIR'] = str(temporary / 'runtime-data')
    app = QApplication.instance() or QApplication([])
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(temporary / 'settings'))
    assert QFontDatabase.addApplicationFont('C:/Windows/Fonts/msyh.ttc') >= 0
    applyDesignerStyle(app)
    QMessageBox.question = lambda *_args, **_kwargs: QMessageBox.Discard
    captures, popups = {}, []

    def capture(widget, name, *, show=True):
        if show:
            widget.show()
            app.processEvents()
        widget.ensurePolished()
        widget.adjustSize() if isinstance(widget, QMessageBox) else None
        assert widget.grab().save(str(output / name))
        captures[name] = dict(width=widget.width(), height=widget.height())

    def popup(box):
        index = len(popups) + 1
        popups.append(dict(title=box.windowTitle(), text=box.text(), details=box.detailedText()))
        # Render the QWidget without the unsupported offscreen native exec loop.
        capture(box, f'popup-{index}.png', show=False)
        next(button for button in box.buttons() if button.text() == '关闭').click()
        return box.result()
    QMessageBox.exec_ = popup
    service = RuntimeService(dbPath=temporary / 'runtime.db', workspaceRoot=temporary / 'jobs')
    client = RuntimeClient(service)
    configuration = connection = None
    try:
        with OwnedDesignerWindows(app) as owner:
            window, configuration, path = legacyDialog(owner, client, temporary)
            window.resize(1280, 720)
            capture(configuration, 'legacy-format.png')
            configuration.validateButton.click()
            assert len(popups) == 1
            configuration.upgradeButton.click()
            waitUntil(lambda: configuration.pending is None)
            assert configuration.initialValidated and len(popups) == 1
            capture(configuration, 'validation-passed.png')
            configuration.reject()
            capture(window, 'main-runtime-summary.png')
            connection = runtime_connection.RuntimeConnectionDialog(window.pageCoordinator)
            waitUntil(lambda: connection.currentPending is None)
            capture(connection, 'current-embedded-runtime.png')
            def refused(_target):
                raise RuntimeClientError('UNAVAILABLE', '模拟候选连接拒绝；未访问网络设备')
            runtime_connection.connectCandidate = refused
            connection.target.setText('127.0.0.1:45678')
            connection.buttons.button(QDialogButtonBox.Ok).click()
            waitUntil(lambda: connection.pending is None)
            assert len(popups) == 2
            capture(connection, 'candidate-failed-current-retained.png')
            connection.reject()
            assert service.loadedDocument is None and not service.jobRepository.all()
            assert path.exists()
        (output / 'render-summary.json').write_text(json.dumps(dict(captures=captures, popups=popups,
            temporary=str(temporary), nativeDesktop='NOT_RUN', modalExec='CONTROLLED_WIDGET_RUNNER'),
            ensure_ascii=False, indent=2), encoding='utf-8')
    finally:
        if configuration is not None and isValid(configuration):
            configuration.reject()
        if connection is not None and isValid(connection):
            connection.reject()
        client.close()
        service.close()


if __name__ == '__main__':
    main()
