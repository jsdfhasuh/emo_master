from __future__ import annotations

from PySide2.QtCore import QEvent, QObject, QTimer
from PySide2.QtWidgets import QApplication, QDialog, QFileDialog


from emo_master.ui.theme import (
    ACCENT as ACCENT, BACKGROUND as BACKGROUND, BORDER as BORDER, MUTED as MUTED, SURFACE as SURFACE, TEXT as TEXT,
    fitWindowToScreen as _fitWindowToScreen, lightPalette, uiFont,
)


def fitWindowToScreen(widget) -> None:
    """Keep the Designer canvas's established client-area sizing."""
    _fitWindowToScreen(widget, containFrame=False)


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
    app.setPalette(lightPalette())
    if not hasattr(app, "_designerSizingFilter"):
        app._designerSizingFilter = _DialogSizingFilter(app)
        app.installEventFilter(app._designerSizingFilter)
