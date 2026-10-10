"""Capture the real Designer toolbar without starting a Runtime or a detection Job."""
from pathlib import Path
import argparse
import json
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--widths', nargs='+', type=int, default=[480, 640, 960, 1280])
    parser.add_argument('--height', type=int, default=500)
    parser.add_argument('--platform', choices=['windows', 'offscreen'], default='windows')
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    os.environ['QT_QPA_PLATFORM'] = args.platform
    import emo_master  # noqa: F401 - initialize dependency DLL search paths
    from PySide2.QtCore import QCoreApplication, QEvent, QEventLoop, QSettings, QTimer, Qt
    from PySide2.QtGui import QGuiApplication
    from PySide2.QtWidgets import QApplication, QToolButton
    from emo_master.apps.designer.main import _configureHighDpi, applyDesignerStyle
    from emo_master.apps.designer.ui.main_window import MainWindow
    from sqlite_writer_validate import candidate

    _configureHighDpi(QCoreApplication, Qt, QGuiApplication)
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(output / 'settings'))
    (output / 'candidate.json').write_text(json.dumps(candidate(), ensure_ascii=False, indent=2), encoding='utf-8')
    app = QApplication([])
    applyDesignerStyle(app)
    calls = {'startJob': 0, 'stopJob': 0}

    def forbidden(name):
        def invoke(*_args, **_kwargs):
            calls[name] += 1
            raise AssertionError('This UI validation must not ' + name)
        return invoke

    values = {}
    settings = SimpleNamespace(value=lambda key, default=None: values.get(key, default),
                               setValue=lambda key, value: values.__setitem__(key, value))
    client = SimpleNamespace(listOperators=lambda: [], startJob=forbidden('startJob'), stopJob=forbidden('stopJob'))
    window = MainWindow(client, settingsStore=settings)
    window.setWindowTitle('EmoMaster Designer — 工具栏验证（未启动任务）')
    window.loadedProjectPath = 'toolbar-validation-only'
    report = {'status': 'FAIL', 'platform': args.platform, 'qtScaleFactor': os.getenv('QT_SCALE_FACTOR'),
              'syntheticControlStateOnly': True, 'cases': [], 'calls': calls}

    def settle():
        for _ in range(6):
            app.processEvents()
        loop = QEventLoop()
        QTimer.singleShot(50, loop.quit)
        loop.exec_()

    try:
        window.show()
        settle()
        screen = window.screen().availableGeometry()
        frame = window.frameGeometry().size() - window.size()
        maxWidth = screen.width() - frame.width() - 16
        maxHeight = screen.height() - frame.height() - 16
        report['screenAvailable'] = [screen.x(), screen.y(), screen.width(), screen.height()]
        report['dpr'] = window.devicePixelRatioF()
        for width in args.widths:
            if args.platform == 'windows' and width > maxWidth:
                report['cases'].append({'requestedWidth': width, 'status': 'NOT_RUN',
                    'reason': 'Requested window exceeds the current physical screen at this Qt scale.'})
                continue
            for running in (False, True):
                window.isJobRunning = running
                window.currentJobId = 'toolbar-validation-only' if running else None
                window.updateToolbarState()
                height = min(args.height, maxHeight) if args.platform == 'windows' else args.height
                window.resize(width, height)
                window.move(screen.x() + 8, screen.y() + 8)
                settle()
                extension = window.mainToolbar.findChild(QToolButton, 'qt_toolbar_ext_button')
                record = {'requested': [width, args.height], 'actual': [window.width(), window.height()],
                          'runningControlState': running, 'startVisible': window.startButton.isVisible(),
                          'stopVisible': window.stopButton.isVisible(), 'startEnabled': window.startButton.isEnabled(),
                          'stopEnabled': window.stopButton.isEnabled(), 'overflowVisible': extension.isVisible(),
                          'frameFitsScreen': screen.contains(window.frameGeometry()), 'status': 'FAIL'}
                report['cases'].append(record)
                assert window.width() == width
                assert record['startVisible'] and record['stopVisible']
                assert record['startEnabled'] is (not running) and record['stopEnabled'] is running
                assert args.platform != 'windows' or record['frameFitsScreen']
                name = str(width) + ('-running.png' if running else '-idle.png')
                assert window.grab().save(str(output / name))
                record.update(status='PASS', screenshot=name)
        assert any(case['status'] == 'PASS' for case in report['cases'])
        assert calls == {'startJob': 0, 'stopJob': 0}
        report['status'] = 'PASS'
    finally:
        window.isJobRunning = False
        window.currentJobId = None
        window.close()
        window.deleteLater()
        settle()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        (output / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
