from PySide2.QtGui import QColor, QFont, QFontDatabase, QPalette
from PySide2.QtWidgets import QApplication


TEXT = "#20242b"
MUTED = "#626b78"
ACCENT = "#2563eb"
BORDER = "#d8dce3"
SURFACE = "#ffffff"
BACKGROUND = "#f5f6f8"

LOG_LEVEL_COLORS = {"ERROR": "#b42318", "WARN": "#854d0e", "INFO": "#1d4ed8", "DEBUG": MUTED}
RUN_STATUS_COLORS = {
    "进行中": LOG_LEVEL_COLORS["INFO"], "运行中": LOG_LEVEL_COLORS["INFO"], "已完成": "#166534",
    "已跳过": MUTED, "已结束": MUTED, "失败": LOG_LEVEL_COLORS["ERROR"],
    "已超时": LOG_LEVEL_COLORS["ERROR"], "达到次数上限": LOG_LEVEL_COLORS["WARN"],
    "停止中": LOG_LEVEL_COLORS["WARN"], "已停止": MUTED,
}


def uiFont(*, bold: bool = False, points: float = 10.5) -> QFont:
    app = QApplication.instance()
    family = getattr(app, "_emoUiFontFamily", None) if app is not None else None
    if not family:
        available = set(QFontDatabase().families())
        family = next((name for name in (
            "Microsoft YaHei UI", "Microsoft YaHei", "Noto Sans CJK SC",
            "Source Han Sans SC", "WenQuanYi Micro Hei", "Segoe UI",
        ) if name in available), QFont().family())
        if app is not None:
            app._emoUiFontFamily = family
    font = QFont(str(family))
    font.setPointSizeF(points)
    font.setWeight(QFont.DemiBold if bold else QFont.Normal)
    font.setStyleStrategy(QFont.PreferAntialias)
    return font


def fitWindowToScreen(widget, *, screen=None, containFrame=True) -> None:
    import shiboken2
    if not shiboken2.isValid(widget):
        return
    if widget.isMaximized() or widget.isFullScreen():
        return
    handle = widget.windowHandle()
    screen = screen or (handle.screen() if handle else QApplication.primaryScreen())
    if screen is None:
        return
    area = screen.availableGeometry().adjusted(12, 36, -12, -12)
    width, height = area.width(), area.height()
    if containFrame:
        frame = widget.frameGeometry()
        width -= frame.width() - widget.width()
        height -= frame.height() - widget.height()
    widget.resize(min(widget.width(), width), min(widget.height(), height))
    frame = widget.frameGeometry()
    x = max(area.left(), min(frame.left(), area.right() - frame.width() + 1))
    y = max(area.top(), min(frame.top(), area.bottom() - frame.height() + 1))
    widget.move(x, y)


def lightPalette() -> QPalette:
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
    return palette
