"""Shared Qt test ownership for top-level windows and nested owned popups."""


def createdWidgetRoots(created):
    """Retire a created popup through any created QWidget ancestor."""
    createdSet = set(created)
    roots = []
    for widget in created:
        parent = widget.parentWidget()
        while parent is not None and parent not in createdSet:
            parent = parent.parentWidget()
        if parent is None:
            roots.append(widget)
    return roots
