"""Selected SQLite paths and bounded asynchronous local image display."""
import sqlite3
import threading
import time

import pytest
from PySide2.QtCore import QCoreApplication, QEvent, QSize, QThreadPool
from PySide2.QtGui import QColor, QImage

from emo_master.core.presentation.models import SqliteSource
from emo_master.core.presentation.sqlite_reader import PathValueError, readPage
from emo_master.ui.presentation.path_image import PathImageView, readPathImage
from emo_master.ui.presentation.sqlite_widgets import SqliteTableView


def waitReady(qtApp, widget):
    deadline = time.monotonic() + 4
    qtApp.processEvents()
    while widget.busy and time.monotonic() < deadline:
        qtApp.processEvents()
        time.sleep(0.005)
    qtApp.processEvents()
    assert not widget.busy


def saveImage(path, color='red', size=(160, 120)):
    image = QImage(*size, QImage.Format_RGB32)
    image.fill(QColor(color))
    assert image.save(str(path))
    return path


def testSelectionReadsDeclaredRawFieldsWithoutDisplayingOrTruncatingThem(tmp_path):
    path = tmp_path / 'history.db'
    with sqlite3.connect(path) as connection:
        connection.execute('CREATE TABLE records (id INTEGER, path, image BLOB)')
        connection.executemany('INSERT INTO records VALUES (?, ?, ?)', [
            (1, 'images/frame.png', b'x' * 100000), (2, None, b''), (3, 42, b''),
            (4, 'x' * 4097, b''), (5, b'x' * 100000, b''), (6, 'x' * 4096, b'')])
    source = SqliteSource(databasePath=str(path), table='records', columns=['id'], orderBy='id', descending=False)
    result = readPage(source, selectionFields=('path', 'missing'))
    assert result.rows == ((1,), (2,), (3,), (4,), (5,), (6,))
    assert result.selectionRows[0]['path'] == 'images/frame.png'
    assert result.selectionRows[1]['path'] is None
    assert all(isinstance(result.selectionRows[index]['path'], PathValueError) for index in (2, 3, 4))
    assert result.selectionRows[5]['path'] == 'x' * 4096
    assert isinstance(result.selectionRows[0]['missing'], PathValueError)
    assert 'image' not in result.selectionRows[0]


def testLocalPathsRelativeBaseAndInvalidInputs(tmp_path):
    path = saveImage(tmp_path / '结果.png')
    image, key = readPathImage(str(path), targetSize=QSize(80, 60))
    assert (image.width(), image.height()) == (80, 60) and key == str(path)
    image, key = readPathImage(path.name, str(tmp_path))
    assert not image.isNull() and key == str(path)
    assert image.size() == QSize(160, 120)
    for value, base, expected in [(path.name, '', '基准目录'), (None, '', '为空'),
                                 (32, '', '文字'), ('https://test/image.png', '', '网络地址'),
                                 (PathValueError('字段错误'), '', '字段错误'),
                                 (str(tmp_path / 'missing.png'), '', '不存在')]:
        with pytest.raises(ValueError, match=expected):
            readPathImage(value, base)
    invalid = tmp_path / 'broken.png'
    invalid.write_text('not an image')
    with pytest.raises(ValueError, match='格式'):
        readPathImage(str(invalid))
    with pytest.raises(ValueError, match='容量'):
        readPathImage(str(path), budget=10)
    largePng = saveImage(tmp_path / 'large.png', size=(2000, 1200))
    with pytest.raises(ValueError, match='容量'):
        readPathImage(str(largePng), targetSize=QSize(800, 480))
    largeJpeg = saveImage(tmp_path / 'large.jpg', size=(2000, 1200))
    scaled, _ = readPathImage(str(largeJpeg), targetSize=QSize(800, 480))
    assert scaled.size() == QSize(800, 480) and scaled.sizeInBytes() <= 8 * 1024 * 1024


def testSelectionSignalClearsOldPictureBeforeReloadAndOnHide(qtApp, tmp_path):
    db = tmp_path / 'history.db'
    path = saveImage(tmp_path / 'photo.png')
    with sqlite3.connect(db) as connection:
        connection.execute('CREATE TABLE records (id INTEGER, path TEXT)')
        connection.executemany('INSERT INTO records VALUES (?, ?)', [(1, path.name), (2, path.name)])
    source = SqliteSource(databasePath=str(db), table='records', columns=['id'], orderBy='id')
    table = SqliteTableView(source, pageSize=1, selectionFields=('path',))
    image = PathImageView(str(tmp_path))
    image.selectionField = 'path'
    table.selectedRowChanged.connect(image.setSelectedRow)
    table.show()
    image.show()
    waitReady(qtApp, table)
    table.table.selectRow(0)
    waitReady(qtApp, image)
    assert table.selectedRow() == {'path': path.name} and not image.image.isNull()
    table.nextButton.click()
    assert table.selectedRow() is None and image.image.isNull()
    waitReady(qtApp, table)
    table.table.selectRow(0)
    waitReady(qtApp, image)
    assert not image.image.isNull()
    table.hide()
    assert table.selectedRow() is None and image.image.isNull()
    table.show()
    table.table.selectRow(0)
    waitReady(qtApp, image)
    table.setPreview(True)
    assert table.selectedRow() is None and image.image.isNull()


def testPreviewAndInvisibleConstructionNeverReadAndFixedPathLoadsOnShow(qtApp, tmp_path, monkeypatch):
    from emo_master.ui.presentation import path_image
    path = saveImage(tmp_path / 'fixed.png')
    calls = []
    original = path_image.readPathImage

    def recorded(*args):
        calls.append(threading.get_ident())
        return original(*args)

    monkeypatch.setattr(path_image, 'readPathImage', recorded)
    view = PathImageView(preview=True)
    view.setPath(str(path))
    qtApp.processEvents()
    assert not calls
    view.show()
    qtApp.processEvents()
    assert not calls
    view.setPreview(False)
    view.setPreview(True)
    qtApp.processEvents()
    assert not calls
    view.setPreview(False)
    waitReady(qtApp, view)
    assert len(calls) == 1 and calls[0] != threading.get_ident() and not view.image.isNull()
    view.hide()
    assert view.image.isNull()
    view.show()
    waitReady(qtApp, view)
    assert len(calls) == 2 and not view.image.isNull()


def testRapidSelectionKeepsOnlyNewestPendingPathAndRejectsLatePreviewResult(qtApp, tmp_path, monkeypatch):
    from emo_master.ui.presentation import path_image
    paths = [saveImage(tmp_path / f'{name}.png', name) for name in ('red', 'green', 'blue')]
    entered, release = threading.Event(), threading.Event()
    calls = []
    original = path_image.readPathImage

    def delayed(*args):
        calls.append(args[0])
        if len(calls) == 1:
            entered.set()
            assert release.wait(2)
        return original(*args)

    monkeypatch.setattr(path_image, 'readPathImage', delayed)
    view = PathImageView()
    view.setPath(str(paths[0]))
    view.show()
    qtApp.processEvents()
    assert entered.wait(1)
    view.setPath(str(paths[1]))
    view.setPath(str(paths[2]))
    qtApp.processEvents()
    release.set()
    waitReady(qtApp, view)
    assert calls == [str(paths[0]), str(paths[2])]
    assert view.image.pixelColor(0, 0) == QColor('blue')
    view.setPath(str(paths[1]))
    view.setPreview(True)
    waitReady(qtApp, view)
    assert view.image.isNull() and '设计示例' in view.message


def testBudgetIsRecheckedOnArrivalAndClosingWorkerCannotUpdateDeletedWidget(qtApp, tmp_path, monkeypatch):
    from emo_master.ui.presentation import path_image
    import shiboken2
    path = saveImage(tmp_path / 'photo.png')
    entered, release = threading.Event(), threading.Event()
    original = path_image.readPathImage

    def delayed(*args):
        entered.set()
        assert release.wait(2)
        return original(*args)

    monkeypatch.setattr(path_image, 'readPathImage', delayed)
    budget = [8 * 1024 * 1024]
    view = PathImageView(availableBytes=lambda: budget[0])
    view.setPath(str(path))
    view.show()
    qtApp.processEvents()
    assert entered.wait(1)
    budget[0] = 0
    release.set()
    waitReady(qtApp, view)
    assert view.image.isNull() and '容量' in view.message
    budget[0] = 8 * 1024 * 1024
    entered.clear()
    release.clear()
    view.reload()
    qtApp.processEvents()
    assert entered.wait(1)
    view.close()
    view.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    assert not shiboken2.isValid(view)
    release.set()
    assert QThreadPool.globalInstance().waitForDone(3000)
    qtApp.processEvents()


@pytest.mark.parametrize('retire', ['preview', 'hide'])
def testImageReadFinishingAfterPreviewOrHideCannotRestorePicture(qtApp, tmp_path, monkeypatch, retire):
    from emo_master.ui.presentation import path_image
    path = saveImage(tmp_path / 'photo.png')
    entered, release = threading.Event(), threading.Event()
    original = path_image.readPathImage

    def delayed(*args):
        entered.set()
        assert release.wait(2)
        return original(*args)

    monkeypatch.setattr(path_image, 'readPathImage', delayed)
    view = PathImageView()
    view.setPath(str(path))
    view.show()
    qtApp.processEvents()
    assert entered.wait(1)
    if retire == 'preview':
        view.setPreview(True)
    else:
        view.hide()
    release.set()
    waitReady(qtApp, view)
    assert view.image.isNull()
    assert ('设计示例' if retire == 'preview' else '隐藏') in view.message
