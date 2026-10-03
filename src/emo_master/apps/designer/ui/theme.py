from __future__ import annotations

from PySide2.QtCore import QEvent, QObject, QTimer
from PySide2.QtGui import QColor, QFont, QFontDatabase, QPalette
from PySide2.QtWidgets import QApplication, QDialog, QFileDialog


TEXT = "#20242b"
MUTED = "#626b78"
ACCENT = "#2563eb"
BORDER = "#d8dce3"
SURFACE = "#ffffff"
BACKGROUND = "#f5f6f8"


def uiFont(*, bold: bool = False, points: float = 10.5) -> QFont:
    app = QApplication.instance()
    family = getattr(app, "_designerFontFamily", None) if app is not None else None
    if not family:
        available = set(QFontDatabase().families())
        family = next((name for name in (
            "Microsoft YaHei UI", "Microsoft YaHei", "Noto Sans CJK SC",
            "Source Han Sans SC", "WenQuanYi Micro Hei", "Segoe UI",
        ) if name in available), QFont().family())
        if app is not None:
            app._designerFontFamily = family
    font = QFont(str(family))
    font.setPointSizeF(points)
    font.setWeight(QFont.DemiBold if bold else QFont.Normal)
    font.setStyleStrategy(QFont.PreferAntialias)
    return font


def fitWindowToScreen(widget) -> None:
    import shiboken2
    if not shiboken2.isValid(widget):
        return
    if widget.isMaximized() or widget.isFullScreen():
        return
    handle = widget.windowHandle()
    screen = handle.screen() if handle else QApplication.primaryScreen()
    if screen is None:
        return
    area = screen.availableGeometry().adjusted(12, 36, -12, -12)
    widget.resize(min(widget.width(), area.width()), min(widget.height(), area.height()))
    frame = widget.frameGeometry()
    x = max(area.left(), min(frame.left(), area.right() - frame.width() + 1))
    y = max(area.top(), min(frame.top(), area.bottom() - frame.height() + 1))
    widget.move(x, y)


class _DialogSizingFilter(QObject):
    def eventFilter(self, obj, event):
        if (event.type() == QEvent.Show and isinstance(obj, QDialog)
                and not isinstance(obj, QFileDialog)):
            QTimer.singleShot(0, lambda: fitWindowToScreen(obj))
            if not obj.property("designerScreenSizing") and obj.windowHandle():
                obj.windowHandle().screenChanged.connect(lambda _screen: QTimer.singleShot(0, lambda: fitWindowToScreen(obj)))
                obj.setProperty("designerScreenSizing", True)
        return False


def configureTheme(app: QApplication) -> None:
    from emo_master.apps.designer.ui import control_icons_rc

    app._designerResources = control_icons_rc
    app.setStyle("Fusion")
    app.setFont(uiFont())
    palette = QPalette()
    for role, color in (
        (QPalette.Window, BACKGROUND), (QPalette.Base, SURFACE),
        (QPalette.AlternateBase, BACKGROUND), (QPalette.WindowText, TEXT),
        (QPalette.Text, TEXT), (QPalette.Button, SURFACE),
        (QPalette.ButtonText, TEXT), (QPalette.Highlight, ACCENT),
        (QPalette.HighlightedText, SURFACE), (QPalette.ToolTipBase, SURFACE),
        (QPalette.ToolTipText, TEXT),
    ):
        palette.setColor(role, QColor(color))
    palette.setColor(QPalette.Disabled, QPalette.Text, QColor("#9299a4"))
    palette.setColor(QPalette.Disabled, QPalette.ButtonText, QColor("#9299a4"))
    app.setPalette(palette)
    if not hasattr(app, "_designerSizingFilter"):
        app._designerSizingFilter = _DialogSizingFilter(app)
        app.installEventFilter(app._designerSizingFilter)
