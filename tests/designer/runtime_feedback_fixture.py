"""Opt-in modal recorder; tests must explicitly request and assert feedback."""
import pytest
from PySide2.QtWidgets import QMessageBox


@pytest.fixture
def messages(monkeypatch):
    recorded = []
    def execute(box):
        recorded.append(dict(title=box.windowTitle(), text=box.text(),
                             detail=box.detailedText(), buttons=[b.text() for b in box.buttons()]))
        return QMessageBox.Close
    monkeypatch.setattr(QMessageBox, 'exec_', execute)
    return recorded
