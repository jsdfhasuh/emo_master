"""Native preview-message layout and context-menu smoke check; synthetic results only."""
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'src'))
import emo_master  # noqa: E402,F401
from PySide2.QtCore import QPoint, QTimer  # noqa: E402
from PySide2.QtGui import QContextMenuEvent  # noqa: E402
from PySide2.QtWidgets import QApplication, QGridLayout, QLabel, QMenu, QWidget  # noqa: E402

from emo_master.ui.presentation import renderer  # noqa: E402
from examples.runtime_pages_p3 import sampleProjectP3  # noqa: E402
from tests.ui.presentation.test_budget_messages import BUSY, unavailableView  # noqa: E402
from tests.ui.presentation.test_latest_sampling_contract import view  # noqa: E402


def main():
    app = QApplication.instance() or QApplication([])
    output = Path(__file__).parent
    showcase = QWidget()
    showcase.setWindowTitle('预览提示验证（模拟结果，无设备连接）')
    showcase.setStyleSheet("QWidget {font-family:'Microsoft YaHei UI'; font-size:13px;}")
    layout = QGridLayout(showcase)
    layout.addWidget(QLabel('模拟结果 · 仅验证提示与复制操作，不代表现场故障复现'), 0, 0, 1, 2)
    windows, images = [], []
    with TemporaryDirectory(prefix='emo-preview-message-') as temporary:
        config = sampleProjectP3(Path(temporary)).presentation
        for index, (title, reason) in enumerate([
            ('通道忙', BUSY),
            ('图片超限', 'raw image 6220800 bytes exceeds source lane limit 4194304; no implicit resize'),
            ('异常后尚未释放', 'image slab quarantined until actual owner retirement'),
            ('旧结果没有具体原因', 'BUDGET_EXCEEDED'),
        ]):
            window = renderer.RuntimePages(config)
            window.submit(unavailableView(window, reason))
            image = window.widgets['overview']['overview-image'][1]
            row, column = divmod(index, 2)
            layout.addWidget(QLabel(title), 1 + row * 2, column)
            layout.addWidget(image, 2 + row * 2, column)
            windows.append(window)
            images.append(image)
        showcase.resize(850, 560)
        showcase.show()
        app.processEvents()
        ratio = showcase.devicePixelRatioF()
        screenshot = output / f'native-messages-{ratio:g}x.png'
        assert showcase.grab().save(str(screenshot))
        selected = images[0].toolTip()
        copied, errors = [], []
        originalApplication = renderer.QApplication
        # Real native menu/QAction, with the user's clipboard left untouched.
        renderer.QApplication = SimpleNamespace(clipboard=lambda: SimpleNamespace(setText=copied.append))

        def choose():
            menu = app.activePopupWidget()
            try:
                assert isinstance(menu, QMenu)
                windows[0].submit(view(windows[0], 3))
                assert images[0].toolTip() == ''
                menu.actions()[0].trigger()
            except Exception as error:
                errors.append(repr(error))
            finally:
                if menu:
                    menu.close()

        try:
            QTimer.singleShot(50, choose)
            position = QPoint(10, 10)
            images[0].contextMenuEvent(QContextMenuEvent(QContextMenuEvent.Mouse, position,
                                                       images[0].mapToGlobal(position)))
            assert not errors, errors
            assert copied == [selected]
        finally:
            renderer.QApplication = originalApplication
            showcase.close()
            for window in windows:
                window.close()
        result = dict(platform=app.platformName(), devicePixelRatio=ratio, screenshot=screenshot.name,
                      nativeMenuCopy='passed', selectedErrorSurvivesLiveRecovery=True,
                      clipboardSink='test spy; native clipboard untouched', realDeviceAccess=False)
        (output / f'native-preview-{ratio:g}x.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
