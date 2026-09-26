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


class ProjectEditSession:
    """One Qt-free draft and atomic save boundary, with bounded whole-project undo.

    Workflow edits use transaction(); page commands use the same transaction log.
    The legacy canvas is not switched to this coordinator until its P4 integration.
    """

    def __init__(self, payload: dict[str, object], *, historyLimit: int = 100) -> None:
        if historyLimit < 1:
            raise ValueError("historyLimit must be positive")
        self.workflows = WorkflowStore(migrateProjectPayload(payload, enablePresentation=True))
        self.historyLimit = historyLimit
        self._undo: list[dict[str, object]] = []
        self._redo: list[dict[str, object]] = []
        self._editing = False
        self._saved = self._signature()
        # An explicit upgrade is a change until persisted, including an empty page set.
        if payload.get("schemaVersion") != "2.2":
            self._saved = "unpersisted-2.2-upgrade"
        from emo_master.apps.designer.state.presentation_store import PresentationStore

        self.presentation = PresentationStore(self)

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
        return json.dumps(payload, sort_keys=True, ensure_ascii=True)

    @property
    def dirty(self) -> bool:
        return self._signature() != self._saved

    @contextmanager
    def transaction(self) -> Iterator[None]:
        if self._editing:
            raise RuntimeError("nested project transactions are not allowed")
        before = self.payload()
        self._editing = True
        try:
            yield
            self.document()  # structure, not publish validity; drafts may be unbound
            if self.payload() != before:
                self._undo.append(before)
                del self._undo[:-self.historyLimit]
                self._redo.clear()
        except BaseException:
            self._restore(before)
            raise
        finally:
            self._editing = False

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
        return True

    def undo(self) -> bool:
        return self._history(self._undo, self._redo)

    def redo(self) -> bool:
        return self._history(self._redo, self._undo)

    def save(self, directory: Path) -> None:
        if self._editing:
            raise RuntimeError("cannot save an unfinished transaction")
        payload = self.workflows.toPayload()
        saveProject(directory, payload)
        self.workflows.commitSavedPayload(payload)
        self._saved = self._signature()
