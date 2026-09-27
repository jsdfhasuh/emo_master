import numpy as np
from PySide2.QtCore import QThread
from PySide2.QtGui import QImage
from PySide2.QtWidgets import QApplication


def assertGuiThread():
    app = QApplication.instance()
    if app is None or QThread.currentThread() != app.thread():
        raise RuntimeError("Qt presentation requires the GUI thread")


def ownedImage(pixels):
    """Convert uint8 GRAY/BGR/BGRA, including strided views, to owned Qt bytes."""
    assertGuiThread()
    if pixels.dtype != np.uint8 or pixels.ndim not in (2, 3) or not 0 < pixels.nbytes <= 8 * 1024 * 1024:
        raise ValueError("unsupported image dtype/shape/budget")
    channels = 1 if pixels.ndim == 2 else pixels.shape[2]
    if channels not in (1, 3, 4):
        raise ValueError("unsupported image channels")
    if channels == 3:
        data = np.ascontiguousarray(pixels[:, :, ::-1])
        format_ = QImage.Format_RGB888
    elif channels == 4:
        data = np.ascontiguousarray(pixels[:, :, [2, 1, 0, 3]])
        format_ = QImage.Format_RGBA8888
    else:
        data = np.ascontiguousarray(pixels.reshape(pixels.shape[:2]))
        format_ = QImage.Format_Grayscale8
    # copy() detaches external numpy storage, including row padding/stride.
    image = QImage(data.data, data.shape[1], data.shape[0], data.strides[0], format_).copy()
    if image.isNull():
        raise ValueError("QImage conversion failed")
    return image
