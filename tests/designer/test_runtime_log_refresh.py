"""Live log batches must not interrupt filter selection or history browsing."""
from dataclasses import replace

import pytest
from PySide2.QtCore import Qt
from PySide2.QtTest import QTest
from PySide2.QtWidgets import QAbstractItemView, QAbstractSlider

from emo_master.apps.designer.ui.log_dialog import LogDialog, StructuredLogEntry
from tests.designer.qt_wait import waitUntil


DYNAMIC_FILTERS = ("jobFilter", "workflowFilter", "nodeFilter", "eventTypeFilter")


def record(index):
    return StructuredLogEntry(
        index + 1, "INFO", f"message-{index:03d}", source="runtime",
        eventType=f"event-{index:03d}", jobId=f"job-{index:03d}",
        projectId="project", workflowId=f"workflow-{index:03d}",
        workflowName=f"Workflow {index:03d}", nodeId=f"node-{index:03d}",
        nodeName=f"Node {index:03d}",
    )


@pytest.fixture
def logView(designerApplication):
    dialog = LogDialog(maximumEntries=100)
    dialog.setEntries([record(index) for index in range(80)])
    dialog.show()
    designerApplication.processEvents()
    yield dialog.view
    dialog.close()


@pytest.mark.parametrize("filterName", DYNAMIC_FILTERS)
def testRepeatedContextDoesNotRebuildFilterOptions(logView, filterName):
    combo = getattr(logView, filterName)
    changes = []
    combo.model().modelReset.connect(lambda: changes.append("reset"))
    combo.model().rowsRemoved.connect(lambda *_: changes.append("remove"))
    combo.model().rowsInserted.connect(lambda *_: changes.append("insert"))
    before = [(combo.itemText(i), combo.itemData(i)) for i in range(combo.count())]

    logView.appendEntry(replace(record(79), timestampMs=100))
    logView._applyPendingEntries()

    assert [(combo.itemText(i), combo.itemData(i)) for i in range(combo.count())] == before
    assert changes == []


@pytest.mark.parametrize("filterName", DYNAMIC_FILTERS)
@pytest.mark.parametrize("newContext", [False, True])
def testOpenFilterKeepsHoveredChoiceAndScrollAcrossLiveBatches(
    logView, designerApplication, filterName, newContext,
):
    combo = getattr(logView, filterName)
    combo.setCurrentIndex(combo.count() - 1)
    combo.showPopup()
    designerApplication.processEvents()
    popup = combo.view()
    hovered = combo.model().index(7, 0)
    popup.setCurrentIndex(hovered)
    popup.scrollTo(hovered, QAbstractItemView.PositionAtCenter)
    designerApplication.processEvents()
    QTest.mouseMove(popup.viewport(), popup.visualRect(hovered).center())
    designerApplication.processEvents()
    assert popup.isVisible()
    assert popup.currentIndex().row() == 7
    expectedText, expectedData = combo.itemText(7), combo.itemData(7)
    oldCount = combo.count()
    oldScroll = popup.verticalScrollBar().value()

    for _ in range(3):
        logView.appendEntry(record(80 if newContext else 79))
        waitUntil(lambda: not logView._pendingEntries)
        assert popup.isVisible()
        assert combo.count() == oldCount
        assert popup.currentIndex().row() == 7
        assert popup.verticalScrollBar().value() == oldScroll

    QTest.mouseClick(popup.viewport(), Qt.LeftButton, pos=popup.visualRect(hovered).center())
    waitUntil(lambda: not popup.isVisible())
    if newContext:
        waitUntil(lambda: combo.count() == oldCount + 1)
    assert combo.currentText() == expectedText
    assert combo.currentData() == expectedData


def testManualScrollPausesFollowingAndCheckboxResumes(logView, designerApplication):
    bar = logView.table.verticalScrollBar()
    assert logView.autoScrollCheck.isChecked()
    assert bar.maximum() > 0
    assert bar.value() == bar.maximum()
    bar.triggerAction(QAbstractSlider.SliderPageStepSub)
    designerApplication.processEvents()
    position = bar.value()
    assert position < bar.maximum()

    logView.appendEntry(record(80))
    waitUntil(lambda: not logView._pendingEntries)

    assert not logView.autoScrollCheck.isChecked()
    assert bar.value() == position
    logView.autoScrollCheck.setChecked(True)
    assert bar.value() == bar.maximum()
    logView.appendEntry(record(81))
    waitUntil(lambda: not logView._pendingEntries)
    assert logView.autoScrollCheck.isChecked()
    assert bar.value() == bar.maximum()


def testCancellingPopupStillRefreshesDeferredOptionsWithoutMoreLogs(logView):
    combo = logView.nodeFilter
    combo.setCurrentIndex(combo.count() - 1)
    selected = combo.currentData()
    count = combo.count()
    combo.showPopup()
    logView.appendEntry(record(80))
    waitUntil(lambda: not logView._pendingEntries)
    assert combo.count() == count

    QTest.keyClick(combo.view(), Qt.Key_Escape)
    waitUntil(lambda: combo.count() == count + 1)

    assert not combo.view().isVisible()
    assert combo.currentData() == selected


def testFollowingStaysEnabledWhenOldRowsAreEvicted(logView):
    for index in range(80, 120):
        logView.appendEntry(record(index))
    waitUntil(lambda: not logView._pendingEntries)
    assert logView.table.rowCount() == 100
    assert logView.autoScrollCheck.isChecked()
    bar = logView.table.verticalScrollBar()
    assert bar.value() == bar.maximum()


def testFollowingSurvivesViewportResize(logView, designerApplication):
    dialog = logView.window()
    for height in (600, 800, 650):
        dialog.resize(1000, height)
        designerApplication.processEvents()
        assert logView.autoScrollCheck.isChecked()
        bar = logView.table.verticalScrollBar()
        assert bar.value() == bar.maximum()


def testHistoryAnchorAndDetailsSurviveOldRowEviction(logView, designerApplication):
    logView.autoScrollCheck.setChecked(False)
    logView.table.setCurrentCell(50, 9)
    logView.detailViewer.selectAll()
    details = logView.detailViewer.textCursor().selectedText()
    logView.table.verticalScrollBar().setValue(40)
    designerApplication.processEvents()
    first = logView.table.item(logView.table.rowAt(0), 9).text()
    selected = logView._detailEntry

    for index in range(80, 110):
        logView.appendEntry(record(index))
    waitUntil(lambda: not logView._pendingEntries)
    designerApplication.processEvents()

    assert logView.table.rowCount() == 100
    assert logView.table.item(logView.table.rowAt(0), 9).text() == first
    assert logView._detailEntry is selected
    assert logView.detailViewer.textCursor().selectedText() == details


def testFilteredOutLogsDoNotReplaceExistingTableItems(logView):
    logView.jobFilter.setCurrentIndex(1)
    item = logView.table.item(0, 9)
    logView.table.setCurrentCell(0, 9)
    selected = logView._detailEntry
    logView.appendEntry(record(80))
    waitUntil(lambda: not logView._pendingEntries)
    assert logView.table.rowCount() == 1
    assert logView.table.item(0, 9) is item
    assert logView._detailEntry is selected


def testPausedHistoryKeepsRowsAndSelectionAcrossOrdinaryAppend(logView, designerApplication):
    logView.autoScrollCheck.setChecked(False)
    logView.table.setCurrentCell(20, 9)
    logView.table.verticalScrollBar().setValue(15)
    designerApplication.processEvents()
    top = logView.table.item(logView.table.rowAt(0), 9)
    selected = logView._detailEntry
    item = logView.table.item(20, 9)

    logView.appendEntry(record(80))
    waitUntil(lambda: not logView._pendingEntries)
    designerApplication.processEvents()

    assert logView.table.item(logView.table.rowAt(0), 9) is top
    assert logView.table.item(20, 9) is item
    assert logView._detailEntry is selected


def testNewNamesUpdateChoicesWithoutChangingSelectedIdentity(logView):
    combo = logView.nodeFilter
    selected = record(79)
    combo.setCurrentIndex(combo.findData(list(selected.nodeKey())))
    logView.appendEntry(replace(selected, nodeName="Renamed node"))
    waitUntil(lambda: not logView._pendingEntries)
    assert combo.currentText() == "Workflow 079 / Renamed node"
    assert combo.currentData() == list(selected.nodeKey())


def testLiveBatchDoesNotScanRetainedLogsForChoicesOrErrorCounts(logView):
    class NoIteration(list):
        def __iter__(self):
            raise AssertionError('ordinary log refresh must only inspect the changed rows')

    logView._entries = NoIteration(logView._entries)
    logView.appendEntry(replace(record(79), level='ERROR'))
    logView._applyPendingEntries()
    assert logView._errorCount == logView._visibleErrorCount == 1
    assert logView.table.rowCount() == 81


def testHiddenWindowKeepsBoundedRecordsWithoutRenderingAndReopensCurrent(logView, designerApplication, monkeypatch):
    dialog = logView.window()
    dialog.hide()
    writes = []
    original = logView._setTableRow
    monkeypatch.setattr(logView, '_setTableRow', lambda *args: (writes.append(args[0]), original(*args)))
    for index in range(80, 400):
        logView.appendEntry(replace(record(index), level='ERROR' if index % 2 else 'INFO'))
    logView._refreshTimer.timeout.emit()
    designerApplication.processEvents()
    assert not writes and not logView._refreshTimer.isActive()
    assert not logView._pendingEntries and not logView._pendingEvicted
    assert len(logView._entries) == 100
    assert logView._errorCount == 50
    dialog.show()
    designerApplication.processEvents()
    assert logView.table.rowCount() == 100
    assert logView.table.item(0, 9).text() == 'message-300'
    assert logView.table.item(99, 9).text() == 'message-399'
    assert logView._visibleErrorCount == 50
    assert logView.jobFilter.findText('job-079') < 0
    assert logView.jobFilter.findText('job-399') > 0


def testHiddenWindowReopenPreservesRetainedHistorySelection(logView, designerApplication):
    logView.autoScrollCheck.setChecked(False)
    logView.table.setCurrentCell(50, 9)
    logView.table.verticalScrollBar().setValue(40)
    selected = logView._detailEntry
    anchor = logView._visibleEntries[40]
    logView.window().hide()
    for index in range(80, 110):
        logView.appendEntry(record(index))
    logView.window().show()
    designerApplication.processEvents()
    assert logView._detailEntry is selected
    assert logView._visibleEntries[logView.table.verticalScrollBar().value()] is anchor


def testIncrementalErrorCountTracksFiltersAndEviction(logView):
    logView.jobFilter.setCurrentIndex(1)
    for index in range(80, 200):
        logView.appendEntry(replace(record(index), level='ERROR'))
    logView._applyPendingEntries()
    assert logView._errorCount == 100 and logView._visibleErrorCount == 0
    assert logView.showErrorsButton.isEnabled()
    logView._resetFilters()
    assert logView._visibleErrorCount == 100
    assert not logView.showErrorsButton.isEnabled()
    logView.clearView()
    assert logView._errorCount == logView._visibleErrorCount == 0
    assert all(not counts for counts in logView._filterCounts.values())
