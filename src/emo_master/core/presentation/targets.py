"""Derive terminal views without changing capture or the editable document."""
from .models import walkComponents


def usesRemotePresentation(presentation):
    return any(page.targets != ['desktop'] or any(
        component.props.sqlite.location == 'runtime' or
        (component.props.imagePath is not None and component.props.imagePath.location == 'runtime')
        for component in walkComponents(page.components)) for page in presentation.pages.values())


def effectiveImageLocation(presentation, pageId, component):
    source = component.props.imagePath
    if source is None:
        raise ValueError('image does not declare a path source')
    if source.kind == 'fixed':
        return source.location
    table = next((item for item in walkComponents(presentation.pages[pageId].components)
                  if item.componentId == source.componentId and item.type == 'sqlite_table'), None)
    if table is None:
        return None  # An unfinished draft keeps its reference; validation reports it.
    return table.props.sqlite.location


def presentationForTarget(presentation, target):
    """Filter a display copy only. Never use this view to prepare capture."""
    if target not in {'desktop', 'web'}:
        raise ValueError('unknown presentation target')
    result = presentation.model_copy(deep=True)
    result.pageOrder = [key for key in result.pageOrder if target in result.pages[key].targets]
    result.pages = {key: result.pages[key] for key in result.pageOrder}
    if result.defaultPageId not in result.pages:
        result.defaultPageId = next(iter(result.pageOrder), None)
    for page in result.pages.values():
        for component in walkComponents(page.components):
            component.actions = {event: action for event, action in component.actions.items()
                                 if action.pageId is None or action.pageId in result.pages}
    return result
