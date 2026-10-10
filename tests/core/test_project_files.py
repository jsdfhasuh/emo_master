import json

import pytest

from emo_master.apps.designer.state.project_store import createProjectSkeleton, loadProject, saveProject
from emo_master.core.project.files import projectFileForSave, resolveProjectFile


@pytest.mark.parametrize("name", ["test.emoproj", "\u4e2d\u6587\u9879\u76ee.EMOPROJ", "project.json"])
def testProjectRoundtripPreservesSelectedFilenameAndBackup(tmp_path, name):
    path = tmp_path / name
    createProjectSkeleton(path, "Example")
    original = path.read_bytes()
    assert resolveProjectFile(path) == path
    assert resolveProjectFile(tmp_path) == path
    payload = loadProject(tmp_path)
    payload["project"]["name"] = "Changed"
    saveProject(tmp_path, payload)
    assert loadProject(path)["project"]["name"] == "Changed"
    assert path.with_suffix(path.suffix + ".bak").read_bytes() == original
    assert {p.name for p in tmp_path.iterdir() if p.is_file()} == {name, name + ".bak"}


def testAmbiguousDirectoryRequiresExactSelectionForLoadAndSave(tmp_path):
    first, second = tmp_path / "first.emoproj", tmp_path / "project.json"
    createProjectSkeleton(first, "First")
    createProjectSkeleton(second, "Second")
    previous = {path: path.read_bytes() for path in (first, second)}
    for action in (resolveProjectFile, projectFileForSave, loadProject):
        with pytest.raises(ValueError, match="multiple project files"):
            action(tmp_path)
    assert loadProject(first)["project"]["name"] == "First"
    assert loadProject(second)["project"]["name"] == "Second"
    assert all(path.read_bytes() == data for path, data in previous.items())


@pytest.mark.parametrize("content", ["not json", "[]", "{}", '{"schemaVersion":"999"}'])
def testExtensionDoesNotBypassContentValidation(tmp_path, content):
    path = tmp_path / "invalid.emoproj"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError, match="invalid project"):
        loadProject(path)


def testUnrelatedJsonIsNotDiscoveredAsAProject(tmp_path):
    (tmp_path / "settings.json").write_text(json.dumps({"enabled": True}))
    with pytest.raises(ValueError, match="project file not found"):
        resolveProjectFile(tmp_path)
    with pytest.raises(ValueError, match="project file not found"):
        resolveProjectFile(tmp_path / "settings.json")
