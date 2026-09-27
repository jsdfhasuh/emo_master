"""Legacy canvas command boundary without a second undo stack."""
from functools import wraps


def draftCommand(method):
    @wraps(method)
    def call(self, *args, **kwargs):
        coordinator = getattr(self, 'pageCoordinator', None)
        if coordinator is None:
            return method(self, *args, **kwargs)
        coordinator.commandDepth += 1
        try:
            return method(self, *args, **kwargs)
        finally:
            coordinator.commandDepth -= 1
            if coordinator.commandDepth == 0:
                coordinator.sync()
    return call
