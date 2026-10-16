"""A detached window may be closed again after its session owner is gone."""
from types import SimpleNamespace

from PySide2.QtCore import QCoreApplication, QEvent, QObject
from PySide2.QtGui import QCloseEvent
from shiboken2 import isValid

from emo_master.ui.presentation.hub import DisplayHub
from emo_master.ui.presentation.renderer import RuntimePages
from examples.runtime_pages_p3 import sampleProjectP3
from tests.ui.presentation.test_renderer import resultView


def testRepeatedPageCloseAfterHubOwnerDeletionDoesNotTouchDeadTimer(qtApp, tmp_path):
    owner = QObject()
    session = SimpleNamespace(readSnapshot=resultView)
    hub = DisplayHub(session, owner)
    window = RuntimePages(sampleProjectP3(tmp_path).presentation, hub=hub)
    assert window in hub.windows
    window.close()
    assert window not in hub.windows
    owner.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    assert not isValid(hub) and not isValid(hub.timer)
    # Teardown/window managers may send another close event to a hidden view.
    event = QCloseEvent()
    window.closeEvent(event)
    assert event.isAccepted()
