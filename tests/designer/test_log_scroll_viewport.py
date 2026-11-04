"""Scrolling must move the visible Qt rows, not just the scrollbar thumb."""
import pytest
from PySide2.QtWidgets import QAbstractSlider

from emo_master.apps.designer.ui.log_dialog import LogDialog, StructuredLogEntry


def records(start, stop):
    return [StructuredLogEntry(index + 1, "INFO", f"message-{index:03d}")
            for index in range(start, stop)]


@pytest.fixture
def viewportLog(designerApplication):
    dialog = LogDialog(maximumEntries=40)
    # Keep several actual rows visible even on the offscreen platform's small
    # virtual screen. Do not infer viewport position from the scrollbar value.
    dialog.view.table.setMinimumHeight(220)
    dialog.show()
    designerApplication.processEvents()
    yield dialog.view
    dialog.close()


def assertLatestVisible(view):
    table = view.table
    bar = table.verticalScrollBar()
    last = table.rowCount() - 1
    rect = table.visualRect(table.model().index(last, 0))
    viewport = table.viewport().rect()
    assert bar.maximum() > 0
    assert bar.value() == bar.maximum()
    assert view.autoScrollCheck.isChecked()
    assert viewport.height() > table.rowHeight(last)
    assert 0 <= rect.top() <= rect.bottom() < viewport.height(), (
        f"latest row {last}: rect={rect.getRect()}, viewport={viewport.getRect()}, "
        f"top row={table.rowAt(0)}, scrollbar={bar.value()}/{bar.maximum()}"
    )
    assert table.rowAt(rect.center().y()) == last


@pytest.mark.parametrize("beforeShow", [False, True])
def testFullRefreshActuallyShowsNewestRow(viewportLog, designerApplication, beforeShow):
    view = viewportLog
    if beforeShow:
        view.window().hide()
    view.setEntries(records(0, 30))
    if beforeShow:
        view.window().show()
    designerApplication.processEvents()
    assertLatestVisible(view)
    view.setEntries(records(0, 35))
    designerApplication.processEvents()
    assertLatestVisible(view)


def testIncrementalBatchesActuallyFollowNewestRow(viewportLog, designerApplication):
    view = viewportLog
    view.setEntries(records(0, 15))
    # Establish a correct initial viewport independently, so this regression
    # exercises incremental append rather than failing at the full refresh.
    view.autoScrollCheck.setChecked(False)
    view.table.scrollToTop()
    view.autoScrollCheck.setChecked(True)
    designerApplication.processEvents()
    assertLatestVisible(view)
    for start in (15, 20, 25, 30, 35, 40):
        for entry in records(start, start + 5):
            view.appendEntry(entry)
        view._applyPendingEntries()
        designerApplication.processEvents()
        assertLatestVisible(view)
    assert view.table.rowCount() == 40


def testFollowingViewportSurvivesFilterResizeAndReopen(viewportLog, designerApplication):
    view = viewportLog
    view.setEntries(records(0, 35))
    view.searchInput.setText("message-0")
    designerApplication.processEvents()
    assertLatestVisible(view)
    for height in (260, 220):
        view.table.setMinimumHeight(height)
        designerApplication.processEvents()
        assertLatestVisible(view)
    view.window().hide()
    for entry in records(35, 45):
        view.appendEntry(entry)
    view.window().show()
    designerApplication.processEvents()
    assertLatestVisible(view)


@pytest.mark.parametrize("update", ["append", "eviction", "refresh", "reopen"])
def testPausedViewportPreservesHistoryAndSelection(viewportLog, designerApplication, update):
    view = viewportLog
    view.setEntries(records(0, 35))
    view.autoScrollCheck.setChecked(False)
    view.table.setCurrentCell(15, 0)
    view.table.verticalScrollBar().setValue(10)
    designerApplication.processEvents()
    anchor = view._visibleEntries[view.table.rowAt(0)]
    selected = view._detailEntry
    root = view.detailViewer.topLevelItem(0)
    detailItem = root.child(0)
    view.detailViewer.setCurrentItem(detailItem)
    if update == "refresh":
        view.searchInput.setText("message-0")
    else:
        if update == "reopen":
            view.window().hide()
        for entry in records(35, 45 if update in {"eviction", "reopen"} else 36):
            view.appendEntry(entry)
        if update == "reopen":
            view.window().show()
        else:
            view._applyPendingEntries()
    designerApplication.processEvents()
    assert not view.autoScrollCheck.isChecked()
    assert view._visibleEntries[view.table.rowAt(0)] is anchor
    assert view._detailEntry is selected
    assert view.detailViewer.currentItem() is detailItem
    assert root.isExpanded()


def testManualScrollAndResumeMoveActualViewport(viewportLog, designerApplication):
    view = viewportLog
    view.setEntries(records(0, 35))
    bar = view.table.verticalScrollBar()
    bar.triggerAction(QAbstractSlider.SliderPageStepSub)
    designerApplication.processEvents()
    assert not view.autoScrollCheck.isChecked()
    top = view.table.rowAt(0)
    assert top < bar.maximum()
    for entry in records(35, 38):
        view.appendEntry(entry)
    view._applyPendingEntries()
    designerApplication.processEvents()
    assert view.table.rowAt(0) == top
    view.autoScrollCheck.setChecked(True)
    designerApplication.processEvents()
    assertLatestVisible(view)
