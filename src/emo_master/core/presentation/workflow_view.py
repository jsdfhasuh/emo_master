"""Derive a Job's pages from existing result scopes, without an ownership table."""
from emo_master.core.presentation.models import walkComponents
from emo_master.core.presentation.validation import pageScopes


def presentationForWorkflow(document, workflowId):
    if workflowId not in document.workflows:
        raise ValueError("workflow not found")
    presentation = document.presentation
    if presentation is None:
        return None
    # Preserve legacy fingerprints and unreferenced definitions for one root.
    if all(scope.entryWorkflowId == workflowId for scope in presentation.resultScopes.values()):
        return presentation
    selected = {key for key, scope in presentation.resultScopes.items() if scope.entryWorkflowId == workflowId}
    pages = {key for key in presentation.pages if pageScopes(presentation, key) <= selected}
    result = presentation.model_copy(deep=True)
    result.pageOrder = [key for key in presentation.pageOrder if key in pages]
    result.pages = {key: value for key, value in result.pages.items() if key in pages}
    result.defaultPageId = (presentation.defaultPageId if presentation.defaultPageId in pages
                            else next(iter(result.pageOrder), None))
    used = set()
    for page in result.pages.values():
        for component in walkComponents(page.components):
            used.update(component.bindings.values())
            component.actions = {key: action for key, action in component.actions.items()
                                 if not action.pageId or action.pageId in pages}
    result.dataSources = {key: value for key, value in result.dataSources.items() if key in used}
    result.resultScopes = {key: value for key, value in result.resultScopes.items() if key in selected}
    return result
