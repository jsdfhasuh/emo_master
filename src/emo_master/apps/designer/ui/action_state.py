"""Command guards shared by menus, keyboard actions and legacy callbacks."""


def flowEditAllowed(window) -> bool:
    coordinator = getattr(window, 'pageCoordinator', None)
    controller = getattr(window, 'runtimeController', None)
    return (not window.isJobRunning
            and not (coordinator is not None and coordinator.pageActive())
            and not (controller is not None and controller._closed))
