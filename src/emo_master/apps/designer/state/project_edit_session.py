from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import json
from pathlib import Path
from collections.abc import Callable, Iterator

from emo_master.apps.designer.state.project_store import loadProject, saveProject
from emo_master.apps.designer.state.workflow_store import WorkflowStore
from emo_master.core.project.migration import migrateProjectPayload
from emo_master.core.project.models import ProjectDocument


def _draftSignature(payload: dict[str, object]) -> str:
    workflows = payload["workflows"]
    assert isinstance(workflows, dict)
    # JSON object equality/canonicalization ignores key order, but interface
    # order is visible on the canvas. Track it only in the editor signature;
    # do not add fields to the saved project or treat other map order as edits.
    interfaceOrder = {
        workflowId: {side: list(workflow[side]) for side in ("inputs", "outputs")}
        for workflowId, workflow in workflows.items()
    }
    return json.dumps((payload, interfaceOrder), sort_keys=True, ensure_ascii=True)


class ProjectEditSession:
    """One Qt-free draft and atomic save boundary, with bounded whole-project undo.

    Workflow edits use transaction(); page commands use the same transaction log.
    The legacy canvas is not switched to this coordinator until its P4 integration.
    """

    def __init__(self, payload: dict[str, object], *, historyLimit: int = 100,
                 enablePresentation: bool = True, workflows: WorkflowStore | None = None) -> None:
        if historyLimit < 1:
            raise ValueError("historyLimit must be positive")
        self.workflows = workflows if workflows is not None else WorkflowStore()
        self.workflows.loadPayload(migrateProjectPayload(payload, enablePresentation=enablePresentation))
        self.historyLimit = historyLimit
        self._undo: list[dict[str, object]] = []
        self._redo: list[dict[str, object]] = []
        self._editing = False
        self._saved = self._signature()
        # An explicit upgrade is a change until persisted, including an empty page set.
        if enablePresentation and payload.get("schemaVersion") not in {"2.2", "2.3", "2.4"}:
            self._saved = "unpersisted-2.2-upgrade"
        from emo_master.apps.designer.state.presentation_store import PresentationStore

        self.presentation = PresentationStore(self)
        self._checkpoint = self.payload()

    def enablePresentation(self) -> None:
        if self.document().presentation is None:
            with self.transaction():
                self._restore(migrateProjectPayload(self.payload(), enablePresentation=True))

    def acceptLoaded(self) -> None:
        """Called only after the shared workflow controller has loaded a project."""
        self._undo.clear()
        self._redo.clear()
        self.markSaved()

    def markSaved(self) -> None:
        self._saved = self._signature()
        self._checkpoint = self.payload()

    def checkpoint(self) -> None:
        """Commit a completed legacy canvas command to the same history."""
        if self._editing:
            return  # the outer transaction validates and records exactly once
        current = self.payload()
        if _draftSignature(current) != _draftSignature(self._checkpoint):
            self.document()
            self._undo.append(self._checkpoint)
            del self._undo[:-self.historyLimit]
            self._redo.clear()
            self._checkpoint = current

    @classmethod
    def load(cls, directory: Path) -> ProjectEditSession:
        return cls(dict(loadProject(directory)))

    def payload(self) -> dict[str, object]:
        payload = self.workflows.toPayload()
        payload["project"] = deepcopy(self.workflows.project)
        return payload

    def document(self) -> ProjectDocument:
        return ProjectDocument.model_validate(self.payload())

    def _signature(self) -> str:
        payload = self.payload()
        metadata = payload["project"]
        assert isinstance(metadata, dict)
        metadata.pop("revision", None)
        metadata.pop("updatedAt", None)
        return _draftSignature(payload)

    @property
    def dirty(self) -> bool:
        return self._signature() != self._saved

    @contextmanager
    def transaction(self) -> Iterator[None]:
        if self._editing:
            raise RuntimeError("nested project transactions are not allowed")
        before = self.payload()
        activeBefore = self.workflows.activeWorkflowId
        self._editing = True
        try:
            yield
            self.document()  # structure, not publish validity; drafts may be unbound
            if _draftSignature(self.payload()) != _draftSignature(before):
                self._undo.append(before)
                del self._undo[:-self.historyLimit]
                self._redo.clear()
        except BaseException:
            self._restore(before)
            if activeBefore in self.workflows.workflows:
                self.workflows.setActiveWorkflow(activeBefore)
            raise
        finally:
            self._editing = False
            self._checkpoint = self.payload()

    def editPresentation(self, edit: Callable) -> None:
        with self.transaction():
            document = self.document()
            assert document.presentation is not None
            edit(document.presentation)
            # Validate after mutation, not model_copy(update=...) which bypasses validation.
            checked = ProjectDocument.model_validate(document.model_dump())
            assert checked.presentation is not None
            self.workflows.projectExtensions["presentation"] = checked.presentation.model_dump()

    def _restore(self, payload: dict[str, object]) -> None:
        active = self.workflows.activeWorkflowId
        self.workflows.loadPayload(payload)
        if active in self.workflows.workflows:
            self.workflows.setActiveWorkflow(active)

    def _history(self, source: list, target: list) -> bool:
        if self._editing:
            raise RuntimeError("cannot change history within a transaction")
        if not source:
            return False
        previous = source.pop()
        current = self.payload()
        # Undo content, not the revision of the last successful disk save.
        metadata = deepcopy(previous["project"])
        metadata["revision"] = self.workflows.project["revision"]
        metadata["updatedAt"] = self.workflows.project["updatedAt"]
        previous["project"] = metadata
        self._restore(previous)
        target.append(current)
        self._checkpoint = self.payload()
        return True

    def undo(self) -> bool:
        return self._history(self._undo, self._redo)

    def redo(self) -> bool:
        return self._history(self._redo, self._undo)

    def recoverCheckpoint(self) -> None:
        """Explicit recovery only, after the UI has exported the invalid draft."""
        if self._editing:
            raise RuntimeError("cannot recover within a transaction")
        self._restore(deepcopy(self._checkpoint))
        self.document()

    def save(self, directory: Path) -> None:
        if self._editing:
            raise RuntimeError("cannot save an unfinished transaction")
        payload = self.workflows.toPayload()
        saveProject(directory, payload)
        self.workflows.commitSavedPayload(payload)
        self._saved = self._signature()
