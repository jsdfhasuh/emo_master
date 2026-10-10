import json
import zipfile

from emo_master.core.project.runtime_package import buildRuntimePackage, installRuntimePackage
from tests.runtime.test_global_variables import boundProject


def testRuntimePackageCarriesDefinitionsAndReferencesNotRuntimeDatabase(tmp_path):
    project = tmp_path / "source"
    project.mkdir()
    payload = boundProject()
    (project / "project.json").write_text(json.dumps(payload), encoding="utf-8")
    (project / "runtime.sqlite3").write_bytes("现场当前值不可发布".encode("utf-8"))
    package = buildRuntimePackage(project, tmp_path / "packages")
    with zipfile.ZipFile(package) as archive:
        assert "runtime.sqlite3" not in archive.namelist()
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["projectSchema"] == "2.4"
        exported = json.loads(archive.read("project.json"))
        assert exported["globalVariables"]["v"]["initialValue"] == .5
    installed = installRuntimePackage(package, tmp_path / "installed")
    assert installed.schemaVersion == "2.4"
    assert installed.workflows["main"].nodes[1].globalVariableBindings[0].variableId == "v"
