import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import emo_master  # noqa: E402,F401
from PySide2.QtWidgets import QApplication  # noqa: E402
from PySide2.QtCore import QCoreApplication, QEvent  # noqa: E402


@pytest.fixture(scope="session")
def qtApp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def cleanWindows(qtApp):
    before = set(qtApp.topLevelWidgets())
    yield
    for widget in qtApp.topLevelWidgets():
        if widget not in before:
            widget.close()
            widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
