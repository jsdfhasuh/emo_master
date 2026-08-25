from __future__ import annotations

import os

import pytest


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session", autouse=True)
def designerApplication():
    try:
        from PySide2.QtWidgets import QApplication
    except ImportError:
        return None

    application = QApplication.instance()
    if application is None:
        application = QApplication([])
    return application
