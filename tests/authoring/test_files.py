from copy import deepcopy
import hashlib
import io
import json
import stat
import zipfile

import pytest

from emo_master.apps.designer.state.project_store import loadProjectDocument
from emo_master.authoring.drafts import DraftService
from emo_master.authoring.errors import AuthoringError
from emo_master.authoring.files import FilesService, resource_purposes


@pytest.fixture
def services(tmp_path):
    drafts = DraftService(tmp_path, catalog=None)
    return drafts, FilesService(drafts)


def _upload(files, state, content=b"image bytes", **kwargs):
    return files.upload(state["draftId"], state["draftRevision"], "input.png", "input_image",
                        content, content_length=len(content), **kwargs)


def _zip(members):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, data in members.items():
            entry = zipfile.ZipInfo()
            entry.filename = name  # preserve raw wire spelling on Windows
            archive.writestr(entry, data)
    return output.getvalue()


def test_upload_revisions_replacement_and_undo_keep_old_bytes(services):
    drafts, files = services
    state = drafts.create("Image project")
    original = deepcopy(state)
    uploaded = _upload(files, state, checksum=hashlib.sha256(b"image bytes").hexdigest())
    assert uploaded["draftRevision"] != state["draftRevision"]
    assert uploaded["sizeBytes"] == len(b"image bytes")
    assert uploaded["sha256"] == hashlib.sha256(b"image bytes").hexdigest()
    assert uploaded["purpose"] in resource_purposes()
    state = drafts.read(state["draftId"])
    resource = state["project"]["resources"]["items"][uploaded["resourceId"]]
    root = drafts.get_draft(state["draftId"]).directory
    first_path = root / resource["path"]
    assert first_path.read_bytes() == b"image bytes"
    assert state["project"]["schemaVersion"] == "2.9"
    assert state["project"]["presentation"]["schemaVersion"] == "1.2"
    with files.snapshot_zip(state["draftId"], state["draftRevision"]) as (candidate, snapshot):
        replaced = _upload(files, state, b"replacement bytes", resource_id=uploaded["resourceId"])
        current = drafts.read(state["draftId"])
        new_resource = current["project"]["resources"]["items"][uploaded["resourceId"]]
        assert new_resource["path"] != resource["path"]
        assert first_path.read_bytes() == b"image bytes"
        with zipfile.ZipFile(candidate) as archive:
            assert archive.read(resource["path"]) == b"image bytes"
        assert snapshot["draftRevision"] == state["draftRevision"]
    assert not candidate.exists()
    undone = drafts.undo(state["draftId"], replaced["draftRevision"])
    assert undone["project"]["resources"]["items"][uploaded["resourceId"]] == resource
    assert first_path.read_bytes() == b"image bytes"
    assert original["project"]["resources"]["items"] == {}


@pytest.mark.parametrize("content_length,checksum,code", [
    (9, None, "UPLOAD_LENGTH_MISMATCH"),
    (None, None, "CONTENT_LENGTH_REQUIRED"),
    (3, "0" * 64, "UPLOAD_DIGEST_MISMATCH"),
])
def test_failed_upload_does_not_register_or_advance(services, content_length, checksum, code):
    drafts, files = services
    state = drafts.create()
    state = drafts.read(state["draftId"])
    with pytest.raises(AuthoringError) as caught:
        files.upload(state["draftId"], state["draftRevision"], "a.png", "input_image", b"abc",
                     content_length=content_length, checksum=checksum)
    assert caught.value.code == code
    assert drafts.read(state["draftId"]) == state
    assert list((drafts.get_draft(state["draftId"]).directory / "assets").glob("*")) == []


def test_stale_upload_rejected_without_new_file(services):
    drafts, files = services
    state = drafts.create()
    _upload(files, state)
    current = drafts.read(state["draftId"])
    assets = list((drafts.get_draft(state["draftId"]).directory / "assets").iterdir())
    with pytest.raises(AuthoringError):
        _upload(files, state, b"stale bytes")
    assert drafts.read(state["draftId"]) == current
    assert list((drafts.get_draft(state["draftId"]).directory / "assets").iterdir()) == assets


def test_fixed_export_roundtrip_does_not_save_or_package_logs(services):
    drafts, files = services
    state = drafts.create("Deliverable")
    uploaded = _upload(files, state)
    state = drafts.read(state["draftId"])
    directory = drafts.get_draft(state["draftId"]).directory
    (directory / "outputs").mkdir(exist_ok=True)
    (directory / "outputs" / "private.log").write_text("not a declared input")
    exported = files.export(state["draftId"], state["draftRevision"], "project-zip")
    assert drafts.read(state["draftId"]) == state
    path, metadata = files.export_content(exported["exportId"])
    frozen = path.read_bytes()
    assert metadata == exported
    assert exported["download"] == dict(method="GET", path=f"/api/project-exports/{exported['exportId']}/content",
                                        authentication="workspace-bearer")
    assert hashlib.sha256(frozen).hexdigest() == exported["sha256"]
    assert exported["sizeBytes"] == len(frozen)
    assert "precheck" not in exported
    assert exported["checkReport"] == {"status": "not_attached", "draftRevision": state["draftRevision"],
                                       "performedByExport": False}
    imported = files.import_project(frozen, "handoff.zip")
    assert imported["draftId"] != state["draftId"]
    assert imported["project"]["project"]["projectId"] == state["project"]["project"]["projectId"]
    imported_payload, original_payload = deepcopy(imported["project"]), deepcopy(state["project"])
    # Import is saved as a new draft through the official save boundary, which
    # advances save metadata while retaining the complete editable document.
    for payload in (imported_payload, original_payload):
        payload["project"].pop("revision")
        payload["project"].pop("updatedAt")
    assert imported_payload == original_payload
    assert not imported["missingInputs"]
    imported_root = drafts.get_draft(imported["draftId"]).directory
    assert loadProjectDocument(imported_root / "project.emoproj").schemaVersion == "2.9"
    with zipfile.ZipFile(io.BytesIO(frozen)) as archive:
        resource_path = state["project"]["resources"]["items"][uploaded["resourceId"]]["path"]
        assert set(archive.namelist()) == {"project.emoproj", resource_path}
    _upload(files, state, b"updated", resource_id=uploaded["resourceId"])
    assert path.read_bytes() == frozen


def test_missing_import_can_export_file_and_candidate_but_not_full_zip(services):
    drafts, files = services
    state = drafts.create()
    uploaded = _upload(files, state)
    state = drafts.read(state["draftId"])
    exported_file = files.export(state["draftId"], state["draftRevision"], "project-file")
    file_path, _ = files.export_content(exported_file["exportId"])
    imported = files.import_project(file_path.read_bytes(), "draft.emoproj")
    assert imported["missingInputs"][0]["resourceId"] == uploaded["resourceId"]
    assert imported["missingInputs"][0]["code"] == "E_RESOURCE_MISSING"
    with pytest.raises(AuthoringError) as caught:
        files.export(imported["draftId"], imported["draftRevision"], "project-zip")
    assert caught.value.code == "INCOMPLETE_EXPORT"
    only_project = files.export(imported["draftId"], imported["draftRevision"], "project-file")
    assert only_project["missingInputs"][0]["resourceId"] == uploaded["resourceId"]
    with files.snapshot_zip(imported["draftId"], imported["draftRevision"]) as (candidate, metadata):
        assert metadata["missingInputs"][0]["resourceId"] == uploaded["resourceId"]
        with zipfile.ZipFile(candidate) as archive:
            assert archive.namelist() == ["project.emoproj"]
    repaired = _upload(files, imported, b"image bytes", resource_id=uploaded["resourceId"])
    complete = files.export(imported["draftId"], repaired["draftRevision"], "project-zip")
    assert not complete["missingInputs"]


@pytest.mark.parametrize("bad_bytes,code", [(b"bad", "E_RESOURCE_SIZE"),
                                            (b"other bytes", "E_RESOURCE_DIGEST")])
def test_provided_corrupt_resource_is_error_not_missing(services, bad_bytes, code):
    drafts, files = services
    state = drafts.create()
    uploaded = _upload(files, state)
    state = drafts.read(state["draftId"])
    resource = state["project"]["resources"]["items"][uploaded["resourceId"]]
    package = _zip({"project.emoproj": json.dumps(state["project"]), resource["path"]: bad_bytes})
    with pytest.raises(AuthoringError) as caught:
        files.import_project(package, "corrupt.zip")
    assert caught.value.code == "RESOURCE_INTEGRITY"
    assert caught.value.details["errors"][0]["resourceId"] == uploaded["resourceId"]
    assert caught.value.details["errors"][0]["code"] == code


@pytest.mark.parametrize("member", ["../outside.txt", "/absolute.txt", "C:/drive.txt", "assets\\bad.txt"])
def test_zip_path_escape_rejected(services, member):
    _, files = services
    with pytest.raises(AuthoringError) as caught:
        files.import_project(_zip({member: b"x"}), "unsafe.zip")
    assert caught.value.code == "INVALID_PROJECT_ZIP"


def test_zip_symlink_and_ambiguous_project_rejected(services):
    drafts, files = services
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        entry = zipfile.ZipInfo("link")
        entry.create_system = 3
        entry.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(entry, "../../outside")
    with pytest.raises(AuthoringError) as caught:
        files.import_project(output.getvalue(), "symlink.zip")
    assert caught.value.code == "INVALID_PROJECT_ZIP"
    project = json.dumps(drafts.create()["project"])
    with pytest.raises(AuthoringError) as caught:
        files.import_project(_zip({"a.emoproj": project, "b.emoproj": project}), "multiple.zip")
    assert caught.value.code == "AMBIGUOUS_PROJECT"


def test_transfer_limit_is_configurable_and_applies_to_unpack(services):
    drafts, _ = services
    files = FilesService(drafts, max_transfer_bytes=1024)
    state = drafts.create()
    with pytest.raises(AuthoringError) as caught:
        _upload(files, state, b"a" * 1025)
    assert caught.value.code == "TRANSFER_TOO_LARGE"
    compressed = io.BytesIO()
    with zipfile.ZipFile(compressed, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("large", b"a" * 2048)
    assert len(compressed.getvalue()) < 1024
    with pytest.raises(AuthoringError) as caught:
        files.import_project(compressed.getvalue(), "large.zip")
    assert caught.value.code == "TRANSFER_TOO_LARGE"


def test_candidate_filename_does_not_collide_with_declared_resource(services):
    drafts, files = services
    state = drafts.create()
    uploaded = _upload(files, state)
    payload = drafts.read(state["draftId"])["project"]
    payload["resources"]["items"][uploaded["resourceId"]]["path"] = "candidate.zip"
    imported = files.import_project(_zip({"project.emoproj": json.dumps(payload),
                                         "candidate.zip": b"image bytes"}), "project.zip")
    with files.snapshot_zip(imported["draftId"], imported["draftRevision"]) as (candidate, _):
        with zipfile.ZipFile(candidate) as archive:
            assert archive.read("candidate.zip") == b"image bytes"
