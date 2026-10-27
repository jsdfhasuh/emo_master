"""Derive a Job's pages from existing result scopes, without an ownership table."""
from emo_master.core.presentation.models import walkComponents
from emo_master.core.presentation.validation import pageScopes


def presentationForWorkflow(document, workflowId, *, capture=False):
    return presentationForWorkflows(document, {workflowId}, capture=capture)


def presentationForWorkflows(document, workflowIds, *, capture=False):
    workflowIds = set(workflowIds)
    if not workflowIds or not workflowIds <= document.workflows.keys():
        raise ValueError("workflow not found")
    presentation = document.presentation
    if presentation is None:
        return None
    # Preserve legacy fingerprints and unreferenced definitions for one root.
    if all(scope.entryWorkflowId in workflowIds for scope in presentation.resultScopes.values()):
        return presentation
    selected = {key for key, scope in presentation.resultScopes.items() if scope.entryWorkflowId in workflowIds}
    pages = {key for key in presentation.pages if pageScopes(presentation, key) <= selected
             or capture and pageScopes(presentation, key) & selected}
    result = presentation.model_copy(deep=True)
    result.pageOrder = [key for key in presentation.pageOrder if key in pages]
    result.pages = {key: value for key, value in result.pages.items() if key in pages}
    result.defaultPageId = (presentation.defaultPageId if presentation.defaultPageId in pages
                            else next(iter(result.pageOrder), None))
    used = set()
    def selectedComponents(components):
        kept = []
        for component in components:
            if any(result.dataSources[key].resultScopeId not in selected for key in component.bindings.values()):
                continue
            component.children = selectedComponents(component.children)
            kept.append(component)
        return kept

    for page in result.pages.values():
        if capture:
            # Each Job captures only its part of a shared producer/consumer page.
            page.components = selectedComponents(page.components)
            page.resultScopeIds = [key for key in page.resultScopeIds if key in selected]
        for component in walkComponents(page.components):
            used.update(component.bindings.values())
            component.actions = {key: action for key, action in component.actions.items()
                                 if (not action.pageId or action.pageId in pages)
                                 and (not action.resultScopeId or action.resultScopeId in selected)}
    result.dataSources = {key: value for key, value in result.dataSources.items() if key in used}
    result.resultScopes = {key: value for key, value in result.resultScopes.items() if key in selected}
    return result
