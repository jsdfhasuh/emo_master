"""A18: bounded Qt ownership exercise, with a synthetic read-only session.

Five pages / 50 controls and one 1080p BGR image are functional inputs only.
This is not the original 0/1/2-observer performance or long-soak acceptance.
The fake session owns a real thread and file handle, but does no RPC/decode.
"""
from dataclasses import replace
import os
import threading
from types import MappingProxyType

import numpy as np
import pytest
import shiboken2
from PySide2.QtCore import QCoreApplication, QEvent, Qt
from PySide2.QtTest import QTest

from emo_master.core.presentation.models import Presentation, walkComponents
from emo_master.core.presentation.results import ClosedSource
from emo_master.ui.presentation.hub import DisplayHub
from emo_master.ui.presentation.renderer import ImageView, RuntimePages
from emo_master.ui.presentation.table import CollectionView
from scripts.r3_resources import resources
from tests.ui.presentation.test_renderer import resultView


def drainDeletes(app):
    app.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()


def fivePageConfiguration():
    sources = {name: {"kind": "node_output", "resultScopeId": "root",
        "workflowId": "main", "nodeId": name, "port": name, "expectedType": kind}
        for name, kind in (("image", "image"), ("count", "integer"),
                           ("decision", "boolean"), ("rows", "collection"))}
    pages = {}
    for number in range(5):
        page = f"page-{number}"
        definitions = [
            {"type": "image", "bindings": {"image": "image"}},
            {"type": "number", "bindings": {"value": "count"}},
            {"type": "text", "bindings": {"value": "count"}},
            {"type": "indicator", "bindings": {"value": "decision"}, "props": {
                "indicatorStates": {"false": {"text": "NG", "color": "red"},
                                    "true": {"text": "OK", "color": "green"}}}},
            {"type": "runtime_status"},
            {"type": "table", "bindings": {"rows": "rows"}, "props": {
                "columns": [{"title": "Value", "fieldPath": ["value"]}], "pageSize": 2}},
            {"type": "navigation_button", "props": {"text": "Next"}, "actions": {
                "clicked": {"type": "navigate", "pageId": f"page-{(number + 1) % 5}"}}},
            {"type": "container", "children": [
                {"componentId": f"{page}-caption", "type": "text", "props": {"text": "离线合成样本"}},
                {"componentId": f"{page}-number", "type": "number", "bindings": {"value": "count"},
                 "layout": {"row": 1}}]},
        ]
        for index, definition in enumerate(definitions):
            definition.update(componentId=f"{page}-{index}", layout={"row": index})
        pages[page] = {"name": page, "components": definitions}
    return Presentation.model_validate({"defaultPageId": "page-0", "pageOrder": list(pages),
        "pages": pages, "dataSources": sources,
        "resultScopes": {"root": {"entryWorkflowId": "main", "scopeWorkflowId": "main"}}})


class OwnedReadOnlyFixture:
    """One explicit owner; window attach/detach cannot create or close it."""
    _pinStore = None

    def __init__(self, view, path):
        self.view = view
        self.stop = threading.Event()
        self.ready = threading.Event()
        self.closed = False
        self.closeCalls = 0
        self.handle = path.open("w+b")
        self.thread = threading.Thread(target=self._work, name="a18-read-only-fixture")
        self.thread.start()
        assert self.ready.wait(2)

    def _work(self):
        self.ready.set()
        self.stop.wait()

    def readSnapshot(self):
        assert not self.closed
        return self.view

    def close(self):
        self.closeCalls += 1
        self.stop.set()
        self.thread.join(2)
        assert not self.thread.is_alive()
        self.handle.close()
        self.closed = True


def nativeCounts(app):
    snapshot = resources(includeThreadIds=True)
    assert snapshot["status"] == "OBSERVED", snapshot
    assert threading.get_native_id() in snapshot["native_thread_ids"], snapshot
    pythonThreads = frozenset(threading.enumerate())
    return {key: snapshot[key] for key in ("handles", "native_threads")} | {
        "native_thread_ids": frozenset(snapshot["native_thread_ids"]),
        "python_threads": len(pythonThreads), "python_thread_objects": pythonThreads,
        "widgets": len(app.allWidgets()), "windows": len(app.allWindows())}


def assertNoNewThreads(before, after):
    # Background threads may retire, but their exits must not hide a new owner.
    for key in ("native_thread_ids", "python_thread_objects"):
        assert after[key] <= before[key], (key, after[key] - before[key])
    for key in ("native_threads", "python_threads"):
        assert after[key] <= before[key], (key, before, after)


def assertStableOwners(app, before, session, owners):
    after = nativeCounts(app)
    # Other tests' background owners may finish during these cycles. Their
    # process-wide handle reclamation is allowed; our file is checked below.
    assert after["handles"] <= before["handles"], ("handles", before, after)
    for key in ("widgets", "windows"):
        assert after[key] == before[key], (key, before, after)
    assertNoNewThreads(before, after)
    thread, threadId, handle, descriptor = owners
    assert session.thread is thread and thread.is_alive()
    assert thread.native_id == threadId and threadId in after["native_thread_ids"]
    assert thread in after["python_thread_objects"]
    assert session.handle is handle and not handle.closed
    assert handle.fileno() == descriptor
    os.fstat(descriptor)  # The owned OS handle must still be valid.
    assert not session.closed and session.closeCalls == 0


def testBackgroundHandleRetirementKeepsOwnedResourceChecks(tmp_path, monkeypatch):
    from types import SimpleNamespace
    thread = threading.current_thread()
    before = {"handles": 10, "widgets": 5, "windows": 1, "native_threads": 1,
              "python_threads": 1, "native_thread_ids": frozenset((thread.native_id,)),
              "python_thread_objects": frozenset((thread,))}
    after = dict(before, handles=9)
    monkeypatch.setattr(__import__(__name__, fromlist=["nativeCounts"]), "nativeCounts", lambda _app: after)
    with (tmp_path / "owned.bin").open("w+b") as handle:
        session = SimpleNamespace(thread=thread, handle=handle, closed=False, closeCalls=0)
        owners = (thread, thread.native_id, handle, handle.fileno())
        assertStableOwners(None, before, session, owners)
        after["widgets"] = 6
        with pytest.raises(AssertionError, match="widgets"):
            assertStableOwners(None, before, session, owners)
        after.update(widgets=5, handles=11)
        with pytest.raises(AssertionError, match="handles"):
            assertStableOwners(None, before, session, owners)
        after["handles"] = 9
        session.closed = True
        with pytest.raises(AssertionError):
            assertStableOwners(None, before, session, owners)


@pytest.mark.parametrize("replaced", ("native_thread_ids", "python_thread_objects"))
def testThreadRetirementCannotMaskNewOwnerWithLowerCount(replaced):
    main, retired, replacement = (threading.Thread() for _ in range(3))
    before = {"native_thread_ids": frozenset((1, 2, 3)),
              "python_thread_objects": frozenset((main, retired)),
              "native_threads": 3, "python_threads": 2}
    after = {"native_thread_ids": frozenset((1,)),
             "python_thread_objects": frozenset((main,)),
             "native_threads": 1, "python_threads": 1}
    assertNoNewThreads(before, after)  # Retirement alone is allowed.
    if replaced == "native_thread_ids":
        after.update(native_thread_ids=frozenset((1, 4)), native_threads=2)
    else:
        after["python_thread_objects"] = frozenset((replacement,))
    assert after["native_threads"] < before["native_threads"]
    assert after["python_threads"] < before["python_threads"]
    with pytest.raises(AssertionError, match=replaced):
        assertNoNewThreads(before, after)


def testThousandNavigationsAndThirtyFloatingCyclesRetireNativeOwners(qtApp, tmp_path):
    config = fivePageConfiguration()
    assert sum(len(list(walkComponents(page.components))) for page in config.pages.values()) == 50
    existingWidgets = set(qtApp.allWidgets())
    existingWindows = set(qtApp.allWindows())
    window = RuntimePages(config)
    pixels = np.full((1080, 1920, 3), (17, 45, 203), np.uint8)
    pixels.flags.writeable = False
    view = resultView(image=pixels, capture=window.expectedCapture)
    scope = view.scopes["root"]
    result = scope.result.model_copy(update={"expectedSourceIds": ("image", "count", "decision", "rows"),
        "sources": (*scope.result.sources,
            ClosedSource(sourceId="image", state="AVAILABLE", image={"resourceId": "synthetic-image",
                "ownerResultKey": scope.result.identity.resultKey, "byteSize": 1, "sha256": "a" * 64,
                "mimeType": "image/png", "provenance": {"frameIdentity": "synthetic-frame",
                    "coordinateSpaceId": "synthetic-space", "trust": "unknown"}}),
            ClosedSource(sourceId="decision", state="AVAILABLE", valueJson="false"),
            ClosedSource(sourceId="rows", state="AVAILABLE", valueJson='[{"value":0},{"value":2},{"value":3}]'))})
    view = replace(view, scopes=MappingProxyType({"root": replace(scope, result=result)}))
    # Qt's platform resources are initialized before measuring repeat ownership.
    window.show()
    drainDeletes(qtApp)
    baseline = nativeCounts(qtApp)
    session = OwnedReadOnlyFixture(view, tmp_path / "owned-session.bin")
    hub = DisplayHub(session)
    window.hub = hub
    hub.attach(window)
    try:
        # Warm every supported control and a second native window once.
        for page in config.pageOrder:
            QTest.mouseClick(window.buttons[page], Qt.LeftButton)
            drainDeletes(qtApp)
        floating = RuntimePages(config, hub=hub)
        floating.show()
        floating.close()
        floating.deleteLater()
        drainDeletes(qtApp)
        assert not shiboken2.isValid(floating)
        before = nativeCounts(qtApp)
        sessionThread = session.thread.native_id
        handle = session.handle.fileno()
        owners = (session.thread, sessionThread, session.handle, handle)
        assertStableOwners(qtApp, before, session, owners)
        for index in range(1000):
            oldPages = dict(window.pages)
            target = config.pageOrder[index % 5]
            QTest.mouseClick(window.buttons[target], Qt.LeftButton)
            drainDeletes(qtApp)
            assert window.currentPageId == target
            assert len(window.pages) <= 2 and window.stack.count() <= 2
            for key, old in oldPages.items():
                if key not in window.pages:
                    assert not shiboken2.isValid(old)
            rows = window.widgets[target]
            image = rows[f"{target}-0"][1]
            assert (image.image.width(), image.image.height()) == (1920, 1080)
            assert image.image.pixelColor(500, 500).getRgb() == (203, 45, 17, 255)
            assert rows[f"{target}-1"][1].text() == "2"
            assert rows[f"{target}-3"][1].text() == "NG"
            assert rows[f"{target}-5"][1].model.rowCount() == 2
            for hidden, controls in window.widgets.items():
                if hidden != target:
                    for _component, widget in controls.values():
                        if isinstance(widget, ImageView):
                            assert widget.image.isNull()
                        elif isinstance(widget, CollectionView):
                            assert widget.model.rowCount() == 0
            if index % 100 == 99:
                assertStableOwners(qtApp, before, session, owners)
        for _ in range(30):
            floating = RuntimePages(config, hub=hub)
            floating.show()
            drainDeletes(qtApp)
            assert len(hub.windows) == 2 and hub.session is session
            floating.close()
            floating.deleteLater()
            drainDeletes(qtApp)
            assert not shiboken2.isValid(floating)
            assert hub.windows == {window}
            assertStableOwners(qtApp, before, session, owners)
        # Hide all observers, advance the fake source, and prove no Qt conversion.
        window.hide()
        converted = hub.conversions
        identity = result.identity.model_copy(update={"resultKey": "result-2", "resultOrdinal": 2})
        newerSources = tuple(source.model_copy(update={"image": source.image.model_copy(
            update={"ownerResultKey": "result-2"})}) if source.image else source for source in result.sources)
        newer = result.model_copy(update={"identity": identity, "sources": newerSources})
        session.view = replace(view, revision=view.revision + 1,
            scopes=MappingProxyType({"root": replace(view.scopes["root"], result=newer)}))
        hub.lastToken = None
        hub.tick()
        assert not window.displayed and hub.conversions == converted == 1
        assert session.thread.native_id == sessionThread and session.handle.fileno() == handle
        assert session.closeCalls == 0
        assertStableOwners(qtApp, before, session, owners)
    finally:
        window.close()
        window.deleteLater()
        hub.deleteLater()
        session.close()
        drainDeletes(qtApp)
    assert not shiboken2.isValid(hub) and not shiboken2.isValid(window)
    assert set(qtApp.allWidgets()) == existingWidgets
    assert set(qtApp.allWindows()) == existingWindows
    assert session.closeCalls == 1 and session.handle.closed and not session.thread.is_alive()
    after = nativeCounts(qtApp)
    assertNoNewThreads(baseline, after)
    assert sessionThread not in after["native_thread_ids"]
    assert session.thread not in after["python_thread_objects"]
    with pytest.raises(OSError):
        os.fstat(handle)
    # All owned thread/handle resources retired, not merely Python references.
    for key in ("handles", "native_threads", "python_threads"):
        # Baseline included the now-destroyed main window's platform resources.
        assert after[key] <= baseline[key], (key, baseline, after)
