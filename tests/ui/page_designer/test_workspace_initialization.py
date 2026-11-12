from PySide2.QtWidgets import QApplication, QLabel

from emo_master.apps.designer.page_designer.tools import EditingTools
from emo_master.apps.designer.page_designer.workspace import EditorPages
from emo_master.core.presentation.models import Component
from test_workspace import designer  # noqa: F401


def testFirstOpenBuildsCatalogAndPageOnlyOnce(designer, monkeypatch):  # noqa: F811
    coordinator = designer.pageCoordinator
    store = coordinator.session.presentation
    page = store.createPage("Overview")
    store.putComponent(page, Component(componentId="value", type="number"))
    before = coordinator.session.payload()
    history = len(coordinator.session._undo)
    catalogCalls, reloadCalls, payloadCalls = [], [], []
    refreshCatalog, reload = EditingTools.refreshCatalog, EditorPages.reload
    toPayload = coordinator.session.workflows.toPayload

    def countPayload(*args, **kwargs):
        payloadCalls.append(None)
        return toPayload(*args, **kwargs)

    def countCatalog(self, **kwargs):
        catalogCalls.append(self)
        return refreshCatalog(self, **kwargs)

    def countReload(self, presentation, **kwargs):
        reloadCalls.append(presentation)
        return reload(self, presentation, **kwargs)

    monkeypatch.setattr(EditingTools, "refreshCatalog", countCatalog)
    monkeypatch.setattr(EditorPages, "reload", countReload)
    monkeypatch.setattr(coordinator.session.workflows, "toPayload", countPayload)
    coordinator.showPages()
    # Bound the synchronous switch; later timers also refresh unrelated chrome.
    assert len(payloadCalls) <= 4  # checkpoint, document, dirty state and history label
    QApplication.processEvents()
    editor = coordinator.editor
    assert coordinator.pageActive()
    assert len(catalogCalls) == 1
    assert not reloadCalls
    assert editor.renderer.currentPageId == page
    assert "value" in editor.renderer.widgets[page]
    assert editor.renderer.cards[page]["value"].findChild(QLabel, "bindingHint") is not None
    assert editor.tools.pageName.text() == "Overview"
    assert editor.pageTabs.count() == 1
    assert editor.tools.destination.count() == 2
    assert coordinator.session.payload() == before
    assert len(coordinator.session._undo) == history
    coordinator.showFlow()
    coordinator.showPages()
    assert len(catalogCalls) == 1 and not reloadCalls

    store.renamePage(page, "Updated")
    coordinator.showPages()
    assert len(catalogCalls) == 2 and len(reloadCalls) == 1
    assert editor.tools.pageName.text() == "Updated"
    assert editor.renderer.config == store.snapshot()


def testFirstOpenEmptyPageSetCanCreateAndRefresh(designer):  # noqa: F811
    coordinator = designer.pageCoordinator
    coordinator.showPages()
    editor = coordinator.editor
    assert editor.empty.isVisible() and not editor.canvas.isVisible()
    page = editor.store.createPage("First")
    editor.refresh()
    assert not editor.empty.isVisible() and editor.canvas.isVisible()
    assert editor.pageId == editor.renderer.currentPageId == page
    assert editor.tools.pageName.text() == "First"


def testAbsentObserverDoesNotBuildAProjectDocument(designer, monkeypatch):  # noqa: F811
    coordinator = designer.pageCoordinator
    presentation = coordinator.session.presentation.snapshot()

    def forbidden():
        raise AssertionError("an absent observer must not serialize the project")

    monkeypatch.setattr(coordinator.session, "document", forbidden)
    coordinator.preview.refreshObserver(presentation)
