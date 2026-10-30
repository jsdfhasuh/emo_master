"""An ImageView that loads a chosen local path without blocking the GUI."""
from __future__ import annotations

import math
from pathlib import Path

from PySide2.QtCore import QByteArray, QBuffer, QIODevice, QObject, QRunnable, QSize, QThreadPool, QTimer, Qt, Signal, Slot
from PySide2.QtGui import QImage, QImageIOHandler, QImageReader
from PySide2.QtWidgets import QMenu

from emo_master.core.presentation.sqlite_reader import PATH_TEXT_LIMIT, PathValueError
from emo_master.core.presentation.local_paths import resolveLocalPath
# RuntimePages imports this module only when building a path-image component,
# after ImageView has been defined. This also keeps the existing hub accounting.
from emo_master.ui.presentation.renderer import ImageView


IMAGE_LIMIT = 8 * 1024 * 1024
FILE_LIMIT = 8 * 1024 * 1024
MAX_SOURCE_PIXELS = 32 * 1024 * 1024


def _path(value, baseDirectory, projectRoot=None):
    if isinstance(value, PathValueError):
        raise ValueError(value.message)
    if value is None or value == '':
        raise ValueError('尚未选择记录，或图片路径为空')
    if not isinstance(value, str):
        raise ValueError('图片路径必须是文字')
    if len(value) > PATH_TEXT_LIMIT or '\x00' in value:
        raise ValueError('图片路径过长或包含无效字符')
    if '://' in value or value.startswith(('\\\\', '//')):
        raise ValueError('仅支持本地图片路径，不支持网络地址')
    path = Path(value).expanduser()
    if not path.is_absolute():
        if '://' in baseDirectory or baseDirectory.startswith(('\\\\', '//')):
            raise ValueError('图片基准目录必须是本地目录')
        if not baseDirectory:
            raise ValueError('相对图片路径需要配置明确的基准目录')
        base = resolveLocalPath(baseDirectory, projectRoot, purpose='图片基准目录')
        path = base / path
        if not path.is_absolute():
            raise ValueError('图片路径与基准目录不能组成完整路径')
    return path


def readPathImage(value, baseDirectory='', targetSize=QSize(1600, 1000), budget=IMAGE_LIMIT, projectRoot=None):
    """Read/decode only in the worker. Never substitute the current directory."""
    path = _path(value, baseDirectory, projectRoot)
    budget = min(IMAGE_LIMIT, budget)
    if budget < 4:
        raise ValueError('界面图像容量不足，请关闭其它图像页面后重试')
    try:
        with path.open('rb') as stream:
            data = stream.read(FILE_LIMIT + 1)
    except FileNotFoundError as error:
        raise ValueError('图片文件不存在：' + str(path)) from error
    if not data or len(data) > FILE_LIMIT:
        raise ValueError('图片为空或超过 8 MiB 文件大小限制')
    buffer = QBuffer()
    buffer.setData(QByteArray(data))
    buffer.open(QIODevice.ReadOnly)
    reader = QImageReader(buffer)
    reader.setAutoTransform(True)
    if bytes(reader.format()).lower() not in {b'png', b'jpeg', b'bmp'}:
        raise ValueError('图片格式不支持或文件损坏，请使用 PNG、JPEG 或 BMP')
    size = reader.size()
    if size.width() <= 0 or size.height() <= 0 or size.width() * size.height() > MAX_SOURCE_PIXELS:
        raise ValueError('图片尺寸无效或超出解码限制')
    format_ = reader.imageFormat()
    depth = QImage(1, 1, format_).depth() if format_ != QImage.Format_Invalid else 32
    depth = max(32, depth)

    def bytesFor(dimensions):
        return ((dimensions.width() * depth + 31) // 32 * 4) * dimensions.height()

    targetBounds = targetSize.expandedTo(QSize(1, 1)).boundedTo(size)
    target = size.scaled(targetBounds, Qt.KeepAspectRatio)
    if bytesFor(target) > budget:
        scale = math.sqrt(budget / bytesFor(target))
        target = QSize(max(1, int(target.width() * scale)), max(1, int(target.height() * scale)))
    # JPEG can downsample during decoding. PNG/BMP scaling may allocate the
    # full source first, so reserve their original decoded plane as well.
    nativeScale = (bytes(reader.format()).lower() == b'jpeg'
                   and reader.supportsOption(QImageIOHandler.ScaledSize))
    if bytesFor(size) > budget and not nativeScale:
        raise ValueError('图片解码超过可用图像容量，请先缩小图片')
    if reader.supportsOption(QImageIOHandler.ScaledSize):
        reader.setScaledSize(target)
    image = reader.read()
    if image.isNull():
        raise ValueError('图片无法解码：' + reader.errorString())
    if image.sizeInBytes() > budget:
        raise ValueError('图片超过可用图像容量，请先缩小图片')
    if image.width() > target.width() or image.height() > target.height():
        image = image.scaled(target, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    return image, str(path)


class _ImageReply(QObject):
    ready = Signal(int, object, str, str)


class _ImageRead(QRunnable):
    def __init__(self, generation, value, base, target, budget, projectRoot):
        super().__init__()
        self.arguments = value, base, target, budget, projectRoot
        self.generation = generation
        self.reply = _ImageReply()

    def run(self):
        try:
            image, key = readPathImage(*self.arguments)
            message = ''
        except (OSError, ValueError, OverflowError) as error:
            image, key, message = QImage(), '', str(error)
        self.reply.ready.emit(self.generation, image, key, message)


class PathImageView(ImageView):
    def __init__(self, baseDirectory='', preview=False, parent=None, *, availableBytes=None, projectRoot=None):
        super().__init__(parent)
        self.baseDirectory, self.preview = baseDirectory, bool(preview)
        self.projectRoot = projectRoot
        self.availableBytes = availableBytes or (lambda: IMAGE_LIMIT)
        self.selectionField = ''
        self._desired, self._generation, self._worker = None, 0, None
        self._closed = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._startLatest)
        self._empty('设计示例 · 不读取本地图片' if self.preview else '尚未选择记录，或图片路径为空')

    @property
    def busy(self):
        return self._worker is not None or self._timer.isActive()

    def setProjectRoot(self, projectRoot):
        if self.projectRoot != projectRoot:
            self.projectRoot = projectRoot
            self.reload()

    def _empty(self, message):
        self.setImage(QImage(), '', message)
        self.setToolTip(message)

    @Slot(object)
    def setSelectedRow(self, row):
        if row is None:
            self.setPath(None)
        elif self.selectionField not in row:
            self.setPath(PathValueError('所选记录缺少图片路径字段'))
        else:
            self.setPath(row[self.selectionField])

    def setPath(self, value):
        if value == self._desired:
            return
        self._desired = value
        self.reload()

    def reload(self):
        self._generation += 1
        self._empty('设计示例 · 不读取本地图片' if self.preview else '等待加载图片')
        if not self.preview:
            try:
                _path(self._desired, self.baseDirectory, self.projectRoot)
            except ValueError as error:
                self._timer.stop()
                self._empty(str(error))
                return
        self._schedule()

    def setPreview(self, preview):
        if self.preview == bool(preview):
            return
        self.preview = bool(preview)
        self.reload()

    def _schedule(self):
        self._timer.stop()
        if not self.preview and not self._closed and self.isVisible():
            # Coalesce changes in one event turn, including intermediate mode
            # changes while switching between design and offline simulation.
            self._timer.start(0)

    def _startLatest(self):
        if self.preview or self._closed or not self.isVisible() or self._worker is not None:
            return
        try:
            _path(self._desired, self.baseDirectory, self.projectRoot)
        except ValueError as error:
            self._empty(str(error))
            return
        budget = max(0, min(IMAGE_LIMIT, int(self.availableBytes())))
        if budget < 4:
            self._empty('界面图像容量不足，请关闭其它图像页面后重试')
            return
        ratio = self.devicePixelRatioF()
        target = QSize(max(1, int(self.width() * ratio)), max(1, int(self.height() * ratio)))
        worker = self._worker = _ImageRead(self._generation, self._desired, self.baseDirectory, target, budget, self.projectRoot)
        worker.reply.ready.connect(self._finished, Qt.QueuedConnection)
        self._empty('正在加载本地图片…')
        QThreadPool.globalInstance().start(worker)

    @Slot(int, object, str, str)
    def _finished(self, generation, image, key, message):
        self._worker = None
        if generation != self._generation:
            self._schedule()
            return
        if self.preview or self._closed or not self.isVisible():
            return
        if message:
            self._empty(message)
            return
        if image.sizeInBytes() > min(IMAGE_LIMIT, max(0, int(self.availableBytes()))):
            self._empty('界面图像容量不足，请关闭其它图像页面后重试')
            return
        self.setImage(image, key)
        self.setToolTip(key)

    def showEvent(self, event):
        super().showEvent(event)
        self._closed = False
        self._schedule()

    def hideEvent(self, event):
        self._generation += 1
        self._timer.stop()
        self._empty('图片已隐藏')
        super().hideEvent(event)

    def dispose(self):
        self._closed = True
        self._generation += 1
        self._timer.stop()
        self._empty('图片已关闭')

    def closeEvent(self, event):
        self.dispose()
        super().closeEvent(event)

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        reloadAction = menu.addAction('重新加载图片')
        reloadAction.setEnabled(not self.preview)
        reloadAction.triggered.connect(self.reload)
        details = self.toolTip()
        if details:
            from PySide2.QtWidgets import QApplication
            menu.addAction('复制提示详情').triggered.connect(
                lambda: QApplication.clipboard().setText(details))
        try:
            menu.exec_(event.globalPos())
        finally:
            menu.deleteLater()
