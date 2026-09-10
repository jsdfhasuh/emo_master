from __future__ import annotations

import os

import pytest


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session", autouse=True)
def designerApplication(tmp_path_factory):
    try:
        from PySide2.QtWidgets import QApplication
        from PySide2.QtCore import QSettings
    except ImportError:
        return None

    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope,
                      str(tmp_path_factory.mktemp("designer-settings")))
    application = QApplication.instance()
    if application is None:
        application = QApplication([])
    from emo_master.apps.designer.main import applyDesignerStyle

    applyDesignerStyle(application)
    return application


@pytest.fixture(autouse=True)
def cleanupDesignerWidgets(designerApplication):
    if designerApplication is None:
        yield
        return
    from PySide2.QtCore import QCoreApplication, QEvent
    import shiboken2

    existing = set(designerApplication.topLevelWidgets())
    yield
    for widget in designerApplication.topLevelWidgets():
        if widget not in existing and shiboken2.isValid(widget):
            widget.close()
            widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
