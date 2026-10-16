"""Legacy canvas command boundary without a second undo stack."""
from copy import deepcopy
from functools import wraps


def _state(window, coordinator):
    # Reading this comparison must not capture unsaved canvas edits into the
    # project. A cancelled/failed import is not an editing command.
    return (coordinator.session._signature(), deepcopy(window.flowModel.toProjectGraph()),
            deepcopy(window.flowScene.getNodePositions()))


def draftCommand(method):
    @wraps(method)
    def call(self, *args, **kwargs):
        coordinator = getattr(self, 'pageCoordinator', None)
        if coordinator is None:
            return method(self, *args, **kwargs)
        outermost = coordinator.commandDepth == 0
        before = _state(self, coordinator) if outermost else None
        selected = self.flowModel.selectedNodeId
        coordinator.commandDepth += 1
        try:
            if not outermost:
                return method(self, *args, **kwargs)
            try:
                with coordinator.session.transaction():
                    result = method(self, *args, **kwargs)
                    if before != _state(self, coordinator):
                        coordinator.sync()
                    return result
            except BaseException:
                # The session has restored content and history. Rebuild the
                # scene from it as well, rather than leaving a poisoned canvas.
                self.workflowController._renderActive()
                self.activeWorkflowId = coordinator.session.workflows.activeWorkflowId
                self._refreshWorkflowTabs()
                if selected in self.flowModel.nodes:
                    self.flowModel.selectNode(selected)
                    self.flowScene.setNodeSelected(selected)
                raise
        finally:
            coordinator.commandDepth -= 1
            if outermost and before != _state(self, coordinator):
                coordinator.chrome.update()
    return call
