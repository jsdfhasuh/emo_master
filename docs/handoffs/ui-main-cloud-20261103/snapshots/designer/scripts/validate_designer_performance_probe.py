"""Isolated Qt smoke capture with a deliberate stall, not a user-lag diagnosis."""
import argparse
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from time import sleep

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
import emo_master  # noqa: E402,F401 - preload native dependencies before Qt
from PySide2.QtCore import QEventLoop, QSettings, QTimer  # noqa: E402
from PySide2.QtGui import QFont, QFontDatabase  # noqa: E402
from PySide2.QtWidgets import QApplication  # noqa: E402
from emo_master.apps.designer.main import applyDesignerStyle  # noqa: E402
from emo_master.apps.designer.ui import main_window  # noqa: E402
from emo_master.core.presentation.models import Component, Props, ResponsiveLayout  # noqa: E402


class Client:
    def listOperators(self):
        return []


def settle(app):
    for _ in range(6):
        app.processEvents()


def syntheticStall():
    # Waiting stalls wall-clock UI response without spending GUI-thread CPU.
    sleep(.18)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'build/designer-probe-smoke')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    applyDesignerStyle(app)
    if sys.platform == 'win32':
        QFontDatabase.addApplicationFont('C:/Windows/Fonts/msyh.ttc')
        app.setFont(QFont('Microsoft YaHei', 9))
    with TemporaryDirectory(prefix='emo-designer-probe-') as temporary:
        os.environ['EMO_DESIGNER_LOG_DIR'] = str(Path(temporary) / 'logs')
        original = main_window.QSettings
        main_window.QSettings = lambda *_: QSettings(str(Path(temporary) / 'settings.ini'), QSettings.IniFormat)
        window = main_window.MainWindow(Client())
        try:
            window.show()
            c = window.pageCoordinator
            c.showPages()
            editor = c.editor
            editor.pageId = editor.store.createPage('性能探针临时示例')

            def configure(p):
                page = p.pages[editor.pageId]
                page.layout = ResponsiveLayout(direction='wrap')
                page.components = [Component(componentId=f'sample-{i}', type='text',
                    props=Props(title=f'示例组件 {i + 1}', text='临时数据'),
                    responsive={'preferredWidth': 220, 'preferredHeight': 96}) for i in range(12)]

            c.session.editPresentation(configure)
            editor.refresh()
            window.resize(1440, 950)
            settle(app)
            before, history = c.session.payload(), len(c.session._undo)
            probe = c.chrome.performanceProbe
            probe.show()
            probe.outputRoot = args.output
            probe.folderLabel.setText(str(args.output.resolve()))
            settle(app)
            probe.start()
            probe.recorder.metadata['fixture'] = 'Synthetic temporary page; deliberate 180ms sleep; not field acceptance'
            for i in range(3):
                editor.tools.responsive.select([f'sample-{i}'])
                editor.canvas.setPreviewSize(768 + i * 100, 720)
                settle(app)
            probe.mark()
            syntheticStall()
            settle(app)
            loop = QEventLoop()
            QTimer.singleShot(120, loop.quit)
            loop.exec_()
            assert probe.dialog.grab().save(str(args.output / 'probe-recording.png'))
            probe.stop()
            settle(app)
            assert probe.dialog.grab().save(str(args.output / 'probe-exported.png'))
            assert c.session.payload() == before and len(c.session._undo) == history
            report = probe.recorder.report()
            assert report['heartbeat']['maxMs'] >= 120
            assert any(any(frame[2] == 'syntheticStall' for frame in sample['frames'])
                       for sample in report['stallStacks'])
            print(json.dumps({'reportDirectory': str(probe.destination),
                'functionCount': len(report['functions']), 'stacks': len(report['stallStacks']),
                'detectedDeliberateStall': True, 'projectUnchanged': True}, ensure_ascii=False))
        finally:
            c.session.markSaved()
            window.close()
            main_window.QSettings = original


if __name__ == '__main__':
    main()
