from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING
from uuid import uuid4

from emo_master.core.presentation.models import (
    Action, Component, DataSource, Page, Presentation, walkComponents,
)
from emo_master.core.presentation.validation import pageScopes

if TYPE_CHECKING:
    from emo_master.apps.designer.state.project_edit_session import ProjectEditSession


def _component(presentation: Presentation, pageId: str, componentId: str) -> Component:
    for component in walkComponents(presentation.pages[pageId].components):
        if component.componentId == componentId:
            return component
    raise KeyError(componentId)


class PresentationStore:
    """Page commands share ProjectEditSession history; view preferences never persist."""

    def __init__(self, session: ProjectEditSession) -> None:
        self._session = session
        self._activePageId: str | None = None
        self.displayedResults: dict[str, dict[str, str]] = {}
        self.frozenResults: dict[str, dict[str, str]] = {}

    def snapshot(self) -> Presentation:
        presentation = self._session.document().presentation
        assert presentation is not None
        return presentation

    @property
    def activePageId(self) -> str | None:
        presentation = self.snapshot()
        return self._activePageId if self._activePageId in presentation.pages else presentation.defaultPageId

    def createPage(self, name: str) -> str:
        pageId = str(uuid4())

        def edit(p: Presentation):
            p.pages[pageId] = Page(name=name)
            p.pageOrder.append(pageId)
            if p.defaultPageId is None:
                p.defaultPageId = pageId

        self._session.editPresentation(edit)
        return pageId

    def renamePage(self, pageId: str, name: str) -> None:
        def edit(p: Presentation):
            p.pages[pageId].name = name
        self._session.editPresentation(edit)

    def reorderPages(self, pageOrder: list[str]) -> None:
        self._session.editPresentation(lambda p: setattr(p, "pageOrder", list(pageOrder)))

    def setDefaultPage(self, pageId: str) -> None:
        self._session.editPresentation(lambda p: setattr(p, "defaultPageId", pageId))

    def putComponent(self, pageId: str, component: Component) -> None:
        def edit(p: Presentation):
            children = p.pages[pageId].components
            for index, existing in enumerate(children):
                if existing.componentId == component.componentId:
                    children[index] = component.model_copy(deep=True)
                    break
            else:
                children.append(component.model_copy(deep=True))
        self._session.editPresentation(edit)

    def copyPage(self, pageId: str, name: str | None = None) -> str:
        copiedId = str(uuid4())

        def edit(p: Presentation):
            copied = p.pages[pageId].model_copy(deep=True)
            copied.name = name or f"{copied.name} (copy)"
            for component in walkComponents(copied.components):
                component.componentId = str(uuid4())
                for action in component.actions.values():
                    if action.pageId == pageId:
                        action.pageId = copiedId
            p.pages[copiedId] = copied
            p.pageOrder.insert(p.pageOrder.index(pageId) + 1, copiedId)
        self._session.editPresentation(edit)
        return copiedId

    def referencesTo(self, pageId: str) -> list[tuple[str, str, str]]:
        return [(owner, component.componentId, event)
                for owner, page in self.snapshot().pages.items() if owner != pageId
                for component in walkComponents(page.components)
                for event, action in component.actions.items() if action.pageId == pageId]

    def deletePage(self, pageId: str, *, repairTo: str | None = None) -> None:
        if self.referencesTo(pageId) and repairTo is None:
            raise ValueError("navigation references require explicit repair or cancellation")

        def edit(p: Presentation):
            if repairTo is not None and (repairTo == pageId or repairTo not in p.pages):
                raise ValueError("invalid replacement page")
            del p.pages[pageId]
            p.pageOrder.remove(pageId)
            for page in p.pages.values():
                for component in walkComponents(page.components):
                    for action in component.actions.values():
                        if action.pageId == pageId:
                            action.pageId = repairTo
                            if (action.resultScopeId and repairTo is not None
                                    and action.resultScopeId not in pageScopes(p, repairTo)):
                                raise ValueError("replacement page does not support detail scope")
            if p.defaultPageId == pageId:
                p.defaultPageId = repairTo or next(iter(p.pageOrder), None)
        self._session.editPresentation(edit)

    def rebind(self, pageId: str, componentId: str, prop: str, source: DataSource | None) -> None:
        def edit(p: Presentation):
            component = _component(p, pageId, componentId)
            if source is None:
                component.bindings.pop(prop, None)
                return
            canonical = DataSource.model_validate(source.model_dump())
            sourceId = next((key for key, value in p.dataSources.items() if value == canonical), None)
            if sourceId is None:
                sourceId = str(uuid4())
                p.dataSources[sourceId] = canonical
            component.bindings[prop] = sourceId
            # Orphans are dropped from current content only; undo owns its detached snapshot.
            used = {key for page in p.pages.values() for item in walkComponents(page.components)
                    for key in item.bindings.values()}
            p.dataSources = {key: value for key, value in p.dataSources.items() if key in used}
        self._session.editPresentation(edit)

    def navigate(self, action: Action) -> None:
        action = Action.model_validate(action.model_dump())
        p = self.snapshot()
        current = self.activePageId
        target = action.pageId if action.type == "navigate" else current
        if current is None or target not in p.pages:
            raise ValueError("navigation page missing")
        if action.type == "resume_live":
            self.frozenResults.pop(target, None)
        elif action.context == "displayed_result" or action.type == "freeze":
            scope = action.resultScopeId
            if scope not in pageScopes(p, current) or scope not in pageScopes(p, target):
                raise ValueError("detail scope unsupported")
            # Capture the result actually displayed by the originating page, never global latest.
            displayed = self.frozenResults.get(current, self.displayedResults.get(current, {}))
            resultKey = displayed.get(scope)
            if resultKey is None:
                raise ValueError("selected scope has no displayed result")
            self.frozenResults[target] = {scope: resultKey}
        else:
            self.frozenResults.pop(target, None)
        self._activePageId = target

    def recordDisplayed(self, pageId: str, scopeId: str, resultKey: str) -> None:
        if scopeId not in pageScopes(self.snapshot(), pageId) or not resultKey:
            raise ValueError("invalid displayed result identity")
        self.displayedResults.setdefault(pageId, {})[scopeId] = deepcopy(resultKey)
