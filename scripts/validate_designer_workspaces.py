"""Native screenshots and geometry checks for both workspaces and preview."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import subprocess
from tempfile import TemporaryDirectory
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
import emo_master  # noqa: E402,F401
from PySide2.QtWidgets import QApplication, QMessageBox  # noqa: E402
from emo_master.apps.designer.main import configureHighDpi, applyDesignerStyle  # noqa: E402
from emo_master.apps.designer.ui.main_window import MainWindow  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--width', type=int, default=1280)
    parser.add_argument('--height', type=int, default=720)
    parser.add_argument('--physical-screen', action='store_true',
                        help='Treat width/height as physical screen pixels; deduct DPI, taskbar and native frame')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    configureHighDpi()
    app = QApplication([])
    applyDesignerStyle(app)
    def settle():
        for _ in range(20):
            app.processEvents()
    with TemporaryDirectory(prefix='emo-workspace-visual-') as data:
        os.environ['EMO_RUNTIME_DATA_DIR'] = data
        prefs = {}
        settings = SimpleNamespace(value=lambda k, d=None: prefs.get(k, d), setValue=lambda k, v: prefs.update({k: v}))
        client = SimpleNamespace(listOperators=lambda: [], loadProject=lambda p: SimpleNamespace(ok=True, message='ok'))
        w = MainWindow(client, settingsStore=settings)
        QMessageBox.question = lambda *a, **k: QMessageBox.Yes
        c = w.pageCoordinator
        try:
            w.resize(args.width, args.height)
            w.show()
            settle()
            target = [args.width, args.height]
            if args.physical_screen:
                screen = app.primaryScreen()
                ratio = w.devicePixelRatioF()
                target = [round(args.width / ratio) - (w.frameGeometry().width() - w.width()),
                          round(args.height / ratio) - (screen.geometry().height() - screen.availableGeometry().height())
                          - (w.frameGeometry().height() - w.height())]
                w.resize(*target)
                settle()
            flowSize = [w.width(), w.height()]
            runVisible = w.startButton.visibleRegion().boundingRect().width() > 0
            assert w.grab().save(str(args.output / 'flow.png'))
            c.showPages()
            settle()
            assert w.grab().save(str(args.output / 'empty.png'))
            e = c.editor
            e.pageId = e.store.createPage('检测总览')
            e.refresh()
            e.tools.fields['columns'].setValue(2)
            e.run(e.tools.apply)
            e.tools.commands().add(e.pageId, 'image', 0, 0)
            number = e.tools.commands().add(e.pageId, 'number', 0, 1)
            e.tools.commands().add(e.pageId, 'text', 1, 0)
            e.tools.commands().add(e.pageId, 'navigation_button', 1, 1)
            e.store.createPage('结果详情')
            e.refresh()
            e.tools.select(number)
            e.tools.fields['title'].setText('检测数量')
            e.tools.fields['unit'].setText('件')
            e.run(e.tools.apply)
            e.tools.select(number)
            settle()
            assert w.grab().save(str(args.output / 'pages.png'))
            preview = c.preview.openObserver()
            settle()
            if args.physical_screen:
                preview.resize(*target)
                settle()
            previewSize = [preview.width(), preview.height()]
            assert preview.grab().save(str(args.output / 'preview.png'))
            c.showFlow()
            settle()
            assert not preview.isVisible()
            c.showPages()
            e.tools.fields['title'].setText('较长的组件标题，用于核对属性栏换行与按钮是否仍然可用')
            e.run(e.tools.apply)
            e.tools.select(number)
            for index in range(10):
                e.store.createPage(f'检测详情 {index + 1}')
            e.refresh()
            settle()
            assert w.grab().save(str(args.output / 'many-pages.png'))
            preview.show()
            settle()
            assert preview.grab().save(str(args.output / 'preview-many-pages.png'))
            preview.hide()
            e.tools.select(number)
            e.tools.fields['fontSize'].setValue(1)
            c.showFlow()
            settle()
            invalidBlocked = c.pageActive() and e.tools.fields['fontSize'].value() == 1
            assert w.grab().save(str(args.output / 'invalid-property.png'))
            e.tools.fields['fontSize'].setValue(16)
            c.sync()
            if e.compact:
                e.togglePanel(0)
                settle()
                assert w.grab().save(str(args.output / 'compact-library.png'))
                e.togglePanel(2)
                settle()
            checks = {
                'requested': [args.width, args.height], 'actual': [w.width(), w.height()],
                'physicalScreen': args.physical_screen, 'targetClient': target,
                'flowActual': flowSize, 'previewActual': previewSize, 'flowRunVisible': runVisible,
                'fitsTarget': (not args.physical_screen or all(actual <= wanted for size in
                    ([w.width(), w.height()], flowSize, previewSize) for actual, wanted in zip(size, target))),
                'dpr': w.devicePixelRatioF(), 'platform': QApplication.platformName(),
                'availableScreen': [app.primaryScreen().availableGeometry().width(), app.primaryScreen().availableGeometry().height()],
                'splitter': e.splitter.sizes(),
                'noHorizontalPropertyScroll': e.propertyScroll.horizontalScrollBar().maximum() == 0,
                'applyVisible': e.tools.applyButton.visibleRegion().boundingRect().height() > 0,
                'selectorVisible': c.chrome.selector.visibleRegion().boundingRect().width() > 0,
                'previewActionVisible': w.mainToolbar.widgetForAction(c.chrome.previewAction).visibleRegion().boundingRect().width() > 0,
                'pageListHeight': e.pageList.height(), 'propertyHeight': e.propertyScroll.height(),
                'previewHiddenInFlow': not preview.isVisible(),
                'invalidInputBlocksSwitch': invalidBlocked,
            }
            changes = subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT).decode('utf-8')
            files = subprocess.check_output(['git', 'ls-files', '-m', '-o', '--exclude-standard'], cwd=ROOT).decode('utf-8').splitlines()
            checks['provenance'] = {
                'head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT).decode().strip(),
                'dirty': changes,
                'sourceSha256': {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                                 for name in files if name.startswith(('src/', 'scripts/')) and (ROOT / name).is_file()},
            }
            (args.output / 'geometry.json').write_text(json.dumps(checks, ensure_ascii=False, indent=2), encoding='utf-8')
            assert all(checks[k] for k in ('noHorizontalPropertyScroll', 'applyVisible', 'selectorVisible',
                'previewActionVisible', 'previewHiddenInFlow', 'flowRunVisible', 'fitsTarget', 'invalidInputBlocksSwitch')), checks
        finally:
            c.session.markSaved()
            w.close()
            settle()


if __name__ == '__main__':
    main()
