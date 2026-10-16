"""Simulated run ownership for layout/shortcut tests; never starts a worker."""


def setRunning(window, running):
    controller = window.runtimeController
    key = window.workflowStore.entryWorkflowId
    run = controller.runs.get(key) or controller._create(key)
    run.jobId = 'fixture-job' if running else None
    run.controller._jobActive = running
    controller.select(key)
