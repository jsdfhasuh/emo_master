"""Qt popups must retire through their complete QWidget ownership ancestry."""
import pytest

from tests.qt_widget_owner import createdWidgetRoots


def testNestedComboPopupRetiresThroughCreatedWindow(designerApplication):
    pytest.importorskip("PySide2")
    import shiboken2
    from PySide2.QtCore import QCoreApplication, QEvent
    from PySide2.QtWidgets import QComboBox, QWidget

    window = QWidget()
    nested = QWidget(window)
    combo = QComboBox(nested)
    combo.addItem("ALL")
    popup = combo.view().window()
    try:
        assert popup.isWindow()
        assert popup in designerApplication.topLevelWidgets()
        assert popup.parentWidget() is combo
        # The direct-parent filter selected BOTH widgets and could schedule
        # independent native deletion of the popup and its owning window.
        created = [window, popup]
        assert [item for item in created if item.parentWidget() not in created] == created
        assert createdWidgetRoots(created) == [window]
        # A new popup under a pre-existing window still needs independent
        # retirement; only ancestors created by this test suppress a root.
        assert createdWidgetRoots([popup]) == [popup]
    finally:
        window.close()
        window.deleteLater()
        QCoreApplication.sendPostedEvents(window, QEvent.DeferredDelete)
        assert not shiboken2.isValid(window)
        assert not shiboken2.isValid(popup)
