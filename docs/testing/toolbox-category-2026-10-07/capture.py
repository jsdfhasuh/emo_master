"""Capture category selection using the formal window and controlled catalog metadata."""
import argparse
import json
import os
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    root = Path(__file__).resolve().parents[3]
    sys.path[:0] = [str(root / 'src'), str(root)]
    os.environ['QT_QPA_PLATFORM'] = 'windows'
    import emo_master  # noqa: F401 - preload DLLs before Qt
    from PySide2.QtCore import QCoreApplication, QEvent, QEventLoop, QSettings, QTimer, Qt
    from PySide2.QtWidgets import QApplication
    from emo_master.apps.designer.main import applyDesignerStyle, configureHighDpi
    from tests.designer.test_category_selection import assertSelection
    from tests.designer.test_floating_toolbox import makeWindow

    configureHighDpi()
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(args.output / 'settings'))
    app = QApplication([])
    applyDesignerStyle(app)
    window, calls = makeWindow()
    report = {'status': 'FAIL', 'qtPlatform': 'windows', 'controlledCatalog': True,
              'realDetection': False, 'jobCalls': calls, 'cases': []}

    def settle():
        app.processEvents()
        loop = QEventLoop()
        QTimer.singleShot(40, loop.quit)
        loop.exec_()

    try:
        window.hide()
        window.setAttribute(Qt.WA_DontShowOnScreen, False)
        window.setWindowTitle('Designer 分类选择验证 · 未启动检测')
        window.resize(1100, 720)
        window.show()
        window.expandSidebar()
        settle()
        from PySide2.QtTest import QTest
        for category, filename in [('全部', 'all'), ('预处理', 'preprocess'),
                                   ('检测', 'detection'), ('其他', 'other')]:
            QTest.mouseClick(window.categoryButtons[category], Qt.LeftButton)
            settle()
            assert window.floatingToolbox.grab().save(str(args.output / (filename + '.png')))
            assert window.grab().save(str(args.output / (filename + '-window.png')))
            assertSelection(window, category)
            report['cases'].append({'category': category, 'status': 'PASS',
                'selected': [name for name, button in window.categoryButtons.items() if button.isChecked()],
                'visibleOperators': window.operatorBubble.getVisibleOperatorIds(),
                'screenshot': filename + '.png'})
        assert calls == []
        report['status'] = 'PASS'
    finally:
        window.close()
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        (args.output / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n',
                                               encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
