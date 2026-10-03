import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace

root = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(root / 'src'))
import emo_master
from PySide2.QtCore import QPoint, Qt, QEventLoop, QTimer
from PySide2.QtWidgets import QApplication, QWidget
from emo_master.apps.designer.main import configureHighDpi, applyDesignerStyle
from emo_master.apps.designer.ui.main_window import MainWindow
from emo_master.apps.designer.ui.widgets import WorkflowTabs

parser = argparse.ArgumentParser()
parser.add_argument('output', type=Path)
args = parser.parse_args()
args.output.mkdir(exist_ok=False)
configureHighDpi()
app = QApplication([])
applyDesignerStyle(app)
def settle():
    loop = QEventLoop()
    QTimer.singleShot(30, loop.quit)
    loop.exec_()

records = []
for name, titles, width, direction in (
    ('single', ['图像检测 [入口]'], 1000, Qt.LeftToRight),
    ('two', ['图像检测 [入口]', '重复检测子流程'], 1000, Qt.LeftToRight),
    ('overflow', ['长名称图像检测子流程 ' + str(i) for i in range(12)], 320, Qt.LeftToRight),
    ('rtl', ['图像检测 [入口]', '重复检测子流程'], 1000, Qt.RightToLeft),
):
    tabs = WorkflowTabs()
    tabs.setObjectName('workflowTabs')
    tabs.setLayoutDirection(direction)
    for title in titles:
        tabs.addTab(QWidget(), title)
    tabs.addTab(QWidget(), '+')
    tabs.resize(width, tabs.sizeHint().height())
    tabs.show()
    settle()
    tabs.grab().save(str(args.output / (name + '.png')))
    bar = tabs.tabBar()
    last = bar.tabRect(len(titles) - 1)
    last.translate(bar.mapTo(tabs, QPoint()))
    button = tabs._addButton.geometry()
    gap = button.left() - last.right() - 1 if direction == Qt.LeftToRight else last.left() - button.right() - 1
    records.append(dict(name=name, width=tabs.width(), dpr=tabs.devicePixelRatioF(), lastTab=last.getRect(),
                        button=button.getRect(), gap=gap, buttonInside=tabs.rect().contains(button)))
    tabs.close()
    tabs.deleteLater()
    settle()

with TemporaryDirectory(prefix='emo-plus-capture-') as data:
    os.environ['EMO_RUNTIME_DATA_DIR'] = data
    prefs = {}
    settings = SimpleNamespace(value=lambda k, d=None: prefs.get(k, d), setValue=lambda k, v: prefs.update({k: v}))
    window = MainWindow(SimpleNamespace(listOperators=lambda: []), settingsStore=settings)
    try:
        window.renameWorkflow('main', '图像检测')
        window.resize(1280, 720)
        window.show()
        settle()
        window.grab().save(str(args.output / 'designer.png'))
        window.workflowTabs.grab().save(str(args.output / 'designer-tabs.png'))
    finally:
        window.pageCoordinator.session.markSaved()
        window.close()
        settle()
result = dict(head=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root).decode().strip(),
              dirty=subprocess.check_output(['git', 'status', '--porcelain'], cwd=root).decode(),
              measurements=records, noRuntimeOrJob=True,
              widgetsSha256=hashlib.sha256((root / 'src/emo_master/apps/designer/ui/widgets.py').read_bytes()).hexdigest())
(args.output / 'measurements.json').write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding='utf-8')
print(json.dumps(records, ensure_ascii=False))
