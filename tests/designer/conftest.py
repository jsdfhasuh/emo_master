from __future__ import annotations

import os

import pytest

from tests.qt_widget_owner import createdWidgetRoots


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
def cleanupDesignerWidgets(designerApplication, monkeypatch):
    if designerApplication is None:
        yield
        return
    from PySide2.QtCore import QCoreApplication, QEvent
    import shiboken2
    from PySide2.QtWidgets import QMessageBox

    # PageCoordinator is now part of the normal Designer. Legacy flow tests
    # deliberately discard their synthetic edits; dedicated page tests cover
    # Save/Discard/Cancel and invalid pending inputs explicitly.
    originalQuestion = QMessageBox.question
    def question(parent, title, *args, **kwargs):
        if title == '项目有未保存修改':
            return QMessageBox.Discard
        return originalQuestion(parent, title, *args, **kwargs)
    monkeypatch.setattr(QMessageBox, 'question', question)

    existing = set(designerApplication.topLevelWidgets())
    yield
    created = [widget for widget in designerApplication.topLevelWidgets()
               if widget not in existing and shiboken2.isValid(widget)]
    # Tool windows can also be children of a main window. Close all owners
    # before scheduling destruction; worker shutdown can pump Qt events.
    # Qt combo/menu popups are top-level windows whose immediate parent is
    # usually a nested control. Never close/delete one independently when a
    # created owner appears anywhere in its QWidget ancestry.
    roots = createdWidgetRoots(created)
    for widget in roots:
        if shiboken2.isValid(widget):
            widget.close()
    for widget in roots:
        if shiboken2.isValid(widget):
            widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
