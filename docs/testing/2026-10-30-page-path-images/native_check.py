"""Native Windows check with generated images, isolated DB and a current project."""
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'src'))
os.environ['QT_QPA_PLATFORM'] = 'windows'
import emo_master  # noqa: E402,F401
from PySide2.QtCore import QCoreApplication, QEvent, Qt, QRect  # noqa: E402
from PySide2.QtGui import QImage, QPainter, QColor, QPen, QFont  # noqa: E402
from PySide2.QtWidgets import QApplication, QMessageBox  # noqa: E402
from emo_master.apps.designer.main import configureHighDpi, applyDesignerStyle  # noqa: E402
from emo_master.apps.designer.state.workflow_store import WorkflowStore  # noqa: E402
from emo_master.apps.designer.state.project_store import saveProject, loadProjectDocument  # noqa: E402
from emo_master.apps.designer.ui.main_window import MainWindow  # noqa: E402
from emo_master.core.project.models import ProjectDocument  # noqa: E402
from emo_master.core.presentation.models import Presentation  # noqa: E402
from emo_master.core.workflow.compiler import WorkflowCompiler  # noqa: E402

OUT = ROOT / 'docs/testing/2026-10-30-page-path-images'
OUT.mkdir(parents=True, exist_ok=True)
configureHighDpi()
app = QApplication([])
applyDesignerStyle(app)


def settle(predicate=lambda: True):
    deadline = time.monotonic() + 10
    for _ in range(8):
        app.processEvents()
    while not predicate():
        assert time.monotonic() < deadline, 'native UI did not settle'
        app.processEvents()
        time.sleep(.01)


def sample(path, annotated=False):
    image = QImage(780, 320, QImage.Format_RGB32)
    image.fill(QColor('#162c3e'))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(QPen(QColor('#56778c'), 2))
    painter.setBrush(QColor('#c9d4dd'))
    for x in (125, 335, 545):
        painter.drawRoundedRect(QRect(x, 82, 110, 154), 16, 16)
        painter.setBrush(QColor('#1d3447'))
        painter.drawEllipse(QRect(x + 30, 112, 50, 50))
        painter.setBrush(QColor('#c9d4dd'))
    painter.setFont(QFont('Microsoft YaHei', 15))
    painter.setPen(QColor('#dce8f0'))
    painter.drawText(QRect(20, 12, 740, 36), Qt.AlignCenter, '生成的演示图片 · 非真实检测')
    if annotated:
        painter.setPen(QPen(QColor('#40df9a'), 3))
        painter.setBrush(Qt.NoBrush)
        for x in (110, 320, 530):
            painter.drawRect(QRect(x, 68, 140, 185))
        painter.setPen(QColor('#40df9a'))
        painter.drawText(QRect(0, 270, 780, 36), Qt.AlignCenter, 'OK   ·   3 个目标   ·   0.98')
    painter.end()
    assert image.save(str(path))


with tempfile.TemporaryDirectory(prefix='emo-page-path-') as directory:
    root = Path(directory)
    os.environ['EMO_RUNTIME_DATA_DIR'] = str(root / 'runtime')
    sample(root / '原图.png')
    sample(root / '结果图.png', True)
    database = root / 'history.sqlite3'
    connection = sqlite3.connect(database)
    try:
        connection.execute('CREATE TABLE 检测记录 (序号 INTEGER PRIMARY KEY, 时间 TEXT, 判定 TEXT, original_image TEXT, result_image TEXT)')
        connection.executemany('INSERT INTO 检测记录 VALUES (?,?,?,?,?)', [
            (i, f'10-30 09:{i:02}:00', 'OK' if i % 4 else 'NG', '原图.png', '结果图.png')
            for i in range(1, 13)])
        connection.commit()
    finally:
        connection.close()
    document = ProjectDocument.model_validate(WorkflowStore().toPayload('历史图片联动演示'))
    document.presentation = Presentation.model_validate(dict(defaultPageId='history', pageOrder=['history'],
        pages={'history': dict(name='历史图片', layout={'columns': 4}, components=[
            dict(componentId='title', type='page_title', layout={'columnSpan': 3},
                 props={'text': '历史查阅 · 点选记录查看图片', 'fontSize': 24, 'fontWeight': 'bold'}),
            dict(componentId='clock', type='clock', layout={'column': 3}, props={'title': '本机时间'}),
            dict(componentId='records', type='sqlite_table', layout={'row': 1, 'rowSpan': 2, 'columnSpan': 2},
                 props={'title': '检测记录', 'pageSize': 8, 'sqlite': dict(databasePath=str(database), table='检测记录',
                     columns=['序号', '时间', '判定'], orderBy='序号')}),
            dict(componentId='original', type='image', layout={'row': 1, 'column': 2, 'columnSpan': 2},
                 props={'title': '原图', 'imagePath': dict(kind='selected_row', componentId='records',
                     field='original_image', baseDirectory=str(root))}),
            dict(componentId='result', type='image', layout={'row': 2, 'column': 2, 'columnSpan': 2},
                 props={'title': '结果图', 'imagePath': dict(kind='selected_row', componentId='records',
                     field='result_image', baseDirectory=str(root))}),
        ])}))
    project = root / 'project' / 'History.emoproj'
    saveProject(project, document.model_dump(mode='json'))
    reloaded = loadProjectDocument(project)
    assert reloaded.schemaVersion == '2.8' and reloaded.presentation == document.presentation
    WorkflowCompiler().compile(reloaded)
    preferences = {}
    settings = SimpleNamespace(value=lambda key, default=None: preferences.get(key, default),
                               setValue=lambda key, value: preferences.update({key: value}))
    client = SimpleNamespace(listOperators=lambda: [], loadProject=lambda path: SimpleNamespace(ok=True, message='ok'))
    window = MainWindow(client, settingsStore=settings)
    QMessageBox.question = lambda *args, **kwargs: QMessageBox.Discard
    try:
        assert window.loadProjectDirectory(str(project))
        window.resize(1400, 900)
        window.show()
        coordinator = window.pageCoordinator
        coordinator.showPages()
        editor = coordinator.editor
        settle()
        editor.tools.select('result')
        editor.floatingProperties.setExpanded(True)
        settle()
        form = editor.tools.imageProperties
        assert form.mode.currentData() == 'path' and form.kind.currentData() == 'selected_row'
        assert form.component.currentData() == 'records' and form.field.currentText() == 'result_image'
        assert editor.renderer.widgets['history']['original'][1].preview
        editor.propertyScroll.ensureWidgetVisible(form.baseDirectory, 0, 70)
        settle()
        assert window.grab().save(str(OUT / 'designer-image-source.png'))
        # Exercise the actual Designer save and load entry points too.
        assert window.saveProjectToDirectory(str(project))
        saved = loadProjectDocument(project)
        assert saved.presentation == document.presentation
        assert window.loadProjectDirectory(str(project))
        coordinator = window.pageCoordinator
        coordinator.showPages()
        observer = coordinator.preview.openObserver()
        observer.resize(1250, 850)
        observer.show()
        observer.source.setCurrentIndex(2)
        settle(lambda: not coordinator.preview.busy and not coordinator.preview.closing)
        widgets = observer.widgets['history']
        table = widgets['records'][1]
        original, result = widgets['original'][1], widgets['result'][1]
        table.refreshButton.click()
        settle(lambda: table.loaded and not table.busy)
        assert table.model.columns == ('序号', '时间', '判定')
        table.table.selectRow(1)
        settle(lambda: not original.image.isNull() and not result.image.isNull())
        assert original.key.endswith('原图.png') and result.key.endswith('结果图.png')
        assert observer.grab().save(str(OUT / 'history-image-preview.png'))
        assert observer.pages['history'].widget().grab().save(str(OUT / 'history-image-page.png'))
        table.nextButton.click()
        assert original.image.isNull() and result.image.isNull()
        settle(lambda: not table.busy)
        observer.source.setCurrentIndex(0)
        settle(lambda: not coordinator.preview.busy and not coordinator.preview.closing)
        assert observer.widgets['history']['original'][1].preview
        assert observer.widgets['history']['original'][1].image.isNull()
        record = dict(platform=app.platformName(), schema=saved.schemaVersion, designerOpen=True,
                      designerSaveReopen=True, roundTrip=True, compile=True, selectedImages=2,
                      hiddenPathColumns=True, pagingClearsImages=True, samplesDoNotReadImages=True,
                      runtimeJobStarted=False, realDeviceUsed=False)
        (OUT / 'native-check.json').write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(record, ensure_ascii=False))
    finally:
        if window.pageCoordinator.preview.observer:
            window.pageCoordinator.preview.observer.close()
        window.close()
        window.deleteLater()
        settle()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
