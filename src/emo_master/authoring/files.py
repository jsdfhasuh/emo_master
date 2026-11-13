"""Byte transfers and snapshot-bound delivery for independent project drafts.

All inputs are received bytes, never a client path. The draft service owns
revisions and transactions; this module retains old resource versions so an
undo record or an in-flight snapshot can keep reading the original bytes.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict
import hashlib
import io
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile
from threading import RLock
from typing import get_args
from uuid import uuid4
import zipfile

from emo_master.apps.designer.state.project_store import loadProjectDocument, saveProject
from emo_master.authoring.errors import AuthoringError
from emo_master.core.project.files import isProjectFile
from emo_master.core.project.models import ProjectDocument
from emo_master.core.project.resources import Resource, safeRelativePath
from emo_master.core.project.snapshots import verifyResources
from emo_master.core.workflow.errors import WorkflowCompileError, locatedValidationError


DEFAULT_TRANSFER_BYTES = 256 * 1024 * 1024


def resource_purposes() -> list[str]:
    """Use the formal resource model's enum in HTTP/MCP discovery as well."""
    return list(get_args(Resource.model_fields["purpose"].annotation))


purpose_values = resource_purposes


def _issue_dict(issue, document):
    result = asdict(issue)
    result["projectId"] = result.get("projectId") or document.project.projectId
    for resource_id in sorted(document.resources.items, key=len, reverse=True):
        if issue.fieldPath.startswith("resources.items." + resource_id + "."):
            result["resourceId"] = resource_id
            break
    return {key: value for key, value in result.items() if value != ""}


def _inventory(document: ProjectDocument, directory: Path):
    """Reuse the owning integrity validator, allowing missing inputs only."""
    if document.resources is None:
        return {}, []
    try:
        return verifyResources(document, directory), []
    except WorkflowCompileError as error:
        issues = [_issue_dict(issue, document) for issue in error.issues]
        invalid = [issue for issue in issues if issue["code"] != "E_RESOURCE_MISSING"]
        if invalid:
            raise AuthoringError("RESOURCE_INTEGRITY", "Provided resource bytes failed integrity checks",
                                 status=422, errors=invalid) from error
        missing_ids = {issue.get("resourceId") for issue in issues}
        available = document.model_copy(deep=True)
        available.resources.items = {key: value for key, value in available.resources.items.items()
                                     if key not in missing_ids}
        return verifyResources(available, directory), issues


def _checked_member(name: str) -> str:
    try:
        return safeRelativePath(name)
    except ValueError as error:
        raise AuthoringError("INVALID_PROJECT_ZIP", "ZIP contains an unsafe member path",
                             fieldPath="archive.members") from error


class FilesService:
    def __init__(self, drafts, *, max_transfer_bytes: int = DEFAULT_TRANSFER_BYTES):
        if max_transfer_bytes <= 0:
            raise ValueError("max_transfer_bytes must be positive")
        self.drafts = drafts
        self.max_transfer_bytes = max_transfer_bytes
        # The export registry points at immutable files. It is not draft history.
        self._exports: dict[str, tuple[Path, dict]] = {}
        self._exports_lock = RLock()

    def _length(self, content: bytes, content_length: int | None) -> None:
        if content_length is None or content_length < 0:
            raise AuthoringError("CONTENT_LENGTH_REQUIRED", "A non-negative Content-Length is required",
                                 status=411, fieldPath="Content-Length")
        if content_length != len(content):
            raise AuthoringError("UPLOAD_LENGTH_MISMATCH", "Received bytes differ from Content-Length",
                                 status=422, fieldPath="Content-Length")
        if len(content) > self.max_transfer_bytes:
            raise AuthoringError("TRANSFER_TOO_LARGE", "Transfer exceeds the configured byte limit",
                                 status=413)

    def upload(self, draft_id: str, expected_revision: str, name: str, purpose: str,
               content: bytes, *, content_length: int | None, checksum: str | None = None,
               resource_id: str | None = None) -> dict:
        self._length(content, content_length)
        if not isinstance(name, str) or not name.strip():
            raise AuthoringError("INVALID_RESOURCE_NAME", "A display name is required", fieldPath="name")
        if purpose not in resource_purposes():
            raise AuthoringError("INVALID_RESOURCE_PURPOSE", "Unknown formal resource purpose",
                                 fieldPath="purpose")
        digest = hashlib.sha256(content).hexdigest()
        if checksum is not None and (not re.fullmatch(r"[0-9a-fA-F]{64}", checksum)
                                     or digest != checksum.lower()):
            raise AuthoringError("UPLOAD_DIGEST_MISMATCH", "Received bytes differ from X-Content-SHA256",
                                 status=422, fieldPath="X-Content-SHA256")
        draft = self.drafts.get_draft(draft_id)
        suffix = PurePosixPath(name.replace("\\", "/")).suffix.lower()
        suffix = suffix if re.fullmatch(r"\.[a-z0-9]{1,12}", suffix) else ""
        relative = "assets/" + uuid4().hex + suffix
        destination = draft.directory / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        temp_name = None
        committed = False
        try:
            with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=".upload-", delete=False) as stream:
                temp_name = Path(stream.name)
                stream.write(content)
            with self.drafts.locked(draft_id, expected_revision) as draft:
                before = draft.session.payload()
                plan = draft.session.document().resources
                if resource_id is not None and resource_id not in plan.items:
                    raise AuthoringError("RESOURCE_NOT_FOUND", "Replacement resource does not exist",
                                         status=404, resourceId=resource_id)
                resource_id = resource_id or "res_" + uuid4().hex
                # The physical name is never reused, even for equal replacement bytes.
                temp_name.replace(destination)
                temp_name = None
                with draft.session.transaction():
                    plan.items[resource_id] = Resource(path=relative, sha256=digest,
                                                       size=len(content), purpose=purpose)
                    draft.session.workflows.projectExtensions["resources"] = plan.model_dump(mode="json")
                self.drafts.commit(draft, before)
                committed = True
                return dict(draftId=draft.draft_id, draftRevision=draft.revision,
                            resourceId=resource_id, name=name, purpose=purpose,
                            sizeBytes=len(content), sha256=digest)
        finally:
            if temp_name is not None:
                temp_name.unlink(missing_ok=True)
            if not committed:
                destination.unlink(missing_ok=True)

    def _unpack(self, content: bytes, directory: Path) -> Path:
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                entries = archive.infolist()
                if sum(entry.file_size for entry in entries) > self.max_transfer_bytes:
                    raise AuthoringError("TRANSFER_TOO_LARGE", "Unpacked project exceeds the configured byte limit",
                                         status=413)
                seen = set()
                for entry in entries:
                    # zipfile normalizes backslashes on Windows; validate the
                    # original wire name before that normalization as well.
                    name = _checked_member(entry.orig_filename.rstrip("/"))
                    if name.casefold() in seen:
                        raise AuthoringError("INVALID_PROJECT_ZIP", "ZIP contains duplicate member paths")
                    seen.add(name.casefold())
                    mode = entry.external_attr >> 16
                    if stat.S_ISLNK(mode) or (stat.S_IFMT(mode) and not (stat.S_ISREG(mode) or stat.S_ISDIR(mode))):
                        raise AuthoringError("INVALID_PROJECT_ZIP", "ZIP links and special files are not allowed")
                    destination = directory / name
                    if entry.is_dir():
                        destination.mkdir(parents=True, exist_ok=True)
                    else:
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        with archive.open(entry) as source, destination.open("xb") as target:
                            shutil.copyfileobj(source, target)
        except AuthoringError:
            raise
        except (zipfile.BadZipFile, OSError, RuntimeError, ValueError) as error:
            raise AuthoringError("INVALID_PROJECT_ZIP", "Project ZIP could not be read", status=422) from error
        candidates = [path for path in directory.rglob("*") if path.is_file() and isProjectFile(path)]
        if len(candidates) != 1:
            raise AuthoringError("AMBIGUOUS_PROJECT", "ZIP must contain exactly one .emoproj or project.json",
                                 status=422, fieldPath="archive.project")
        return candidates[0]

    def import_project(self, content: bytes, filename: str, *, content_length: int | None = None) -> dict:
        self._length(content, len(content) if content_length is None else content_length)
        with tempfile.TemporaryDirectory(prefix="emo-authoring-import-") as temporary:
            directory = Path(temporary)
            if filename.lower().endswith(".zip"):
                project_path = self._unpack(content, directory)
            elif isProjectFile(Path(filename.replace("\\", "/"))):
                project_path = directory / "project.emoproj"
                project_path.write_bytes(content)
            else:
                raise AuthoringError("INVALID_PROJECT_FORMAT", "Import requires .emoproj, project.json or ZIP")
            try:
                document = loadProjectDocument(project_path)
            except ValueError as error:
                located = locatedValidationError(error.__cause__ or error, "E_PROJECT_INVALID")
                issues = [{key: value for key, value in asdict(issue).items() if value != ""}
                          for issue in located.issues]
                raise AuthoringError("INVALID_PROJECT", "Imported project is not a valid formal document",
                                     status=422, fieldPath="project", errors=issues) from error
            resolved, missing = _inventory(document, project_path.parent)
            state = self.drafts.create(document.project.name, payload=document.model_dump(mode="json"))
            with self.drafts.locked(state["draftId"]) as draft:
                for resource_id, source in resolved.items():
                    target = draft.directory / document.resources.items[resource_id].path
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, target)
            return dict(state, missingInputs=missing)

    def _freeze(self, draft_id: str, expected_revision: str, directory: Path):
        payload, revision, source_root = self.drafts.snapshot(draft_id, expected_revision)
        document = ProjectDocument.model_validate(payload)
        resolved, missing = _inventory(document, source_root)
        project_path = directory / "project.emoproj"
        saveProject(project_path, deepcopy(payload))
        # Use the same loader used by Designer, not JSON-only roundtrip validation.
        reloaded = loadProjectDocument(project_path)
        if reloaded != document:
            raise AuthoringError("EXPORT_ROUNDTRIP", "Formal project save/reload changed its content", status=500)
        for resource_id, source in resolved.items():
            target = directory / document.resources.items[resource_id].path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        return document, revision, missing

    @staticmethod
    def _write_zip(directory: Path, document: ProjectDocument, destination: Path):
        # Explicit declarations only; never walk logs, outputs, .bak or history.
        with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.write(directory / "project.emoproj", "project.emoproj")
            for resource in document.resources.items.values() if document.resources else []:
                source = directory / resource.path
                if source.is_file():
                    archive.write(source, resource.path)

    def export(self, draft_id: str, expected_revision: str, format: str) -> dict:
        if format not in {"project-file", "project-zip"}:
            raise AuthoringError("INVALID_EXPORT_FORMAT", "Expected project-file or project-zip", fieldPath="format")
        draft = self.drafts.get_draft(draft_id)
        export_id = uuid4().hex
        # Sibling export storage is independent of any live draft asset directory.
        exports_base = (draft.directory.parent.parent / "project-exports").resolve()
        export_root = exports_base / export_id
        export_root.mkdir(parents=True, exist_ok=False)
        successful = False
        try:
            with tempfile.TemporaryDirectory(prefix=".snapshot-", dir=export_root) as temporary:
                document, revision, missing = self._freeze(draft_id, expected_revision, Path(temporary))
                if format == "project-zip" and missing:
                    raise AuthoringError("INCOMPLETE_EXPORT", "Complete project ZIP requires all declared resources",
                                         status=422, draftId=draft_id, draftRevision=revision, missingInputs=missing)
                filename = "project.zip" if format == "project-zip" else "project.emoproj"
                destination = export_root / filename
                if format == "project-zip":
                    self._write_zip(Path(temporary), document, destination)
                else:
                    shutil.copyfile(Path(temporary) / "project.emoproj", destination)
                content = destination.read_bytes()
                metadata = dict(exportId=export_id, draftId=draft_id, draftRevision=revision,
                                filename=filename,
                                mediaType="application/zip" if format == "project-zip" else "application/json",
                                sizeBytes=len(content), sha256=hashlib.sha256(content).hexdigest(),
                                missingInputs=missing,
                                precheck=dict(status="not_run", draftRevision=revision),
                                checkReport=dict(status="not_checked", draftRevision=revision),
                                download=dict(method="GET", path=f"/api/project-exports/{export_id}/content",
                                              authentication="workspace-bearer"))
            with self._exports_lock:
                self._exports[export_id] = (destination, deepcopy(metadata))
            successful = True
            return metadata
        finally:
            if not successful:
                # This root is generated above, inside managed project-exports.
                if export_root.resolve().parent != exports_base:
                    raise RuntimeError("export cleanup escaped its managed directory")
                shutil.rmtree(export_root)

    def export_content(self, export_id: str) -> tuple[Path, dict]:
        with self._exports_lock:
            try:
                path, metadata = self._exports[export_id]
            except KeyError as error:
                raise AuthoringError("EXPORT_NOT_FOUND", "Export does not exist", status=404) from error
            return path, deepcopy(metadata)

    @contextmanager
    def snapshot_zip(self, draft_id: str, expected_revision: str):
        """Yield a candidate package until its actual consumer has finished.

        Missing declaration bytes remain missing for target INCOMPLETE reports;
        provided corrupt bytes are an error, never silently dropped.
        """
        with tempfile.TemporaryDirectory(prefix="emo-authoring-candidate-") as temporary:
            directory = Path(temporary)
            project_root = directory / "project"
            project_root.mkdir()
            document, revision, missing = self._freeze(draft_id, expected_revision, project_root)
            path = directory / "candidate.zip"
            self._write_zip(project_root, document, path)
            content = path.read_bytes()
            yield path, dict(draftId=draft_id, draftRevision=revision,
                             projectId=document.project.projectId, missingInputs=missing,
                             sizeBytes=len(content), sha256=hashlib.sha256(content).hexdigest())
