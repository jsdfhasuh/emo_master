from pathlib import Path
import hashlib
import json
import zipfile

from emo_master.core.project.package_builder import buildPackage


def testBuildPackageCreatesArchive(tmp_path: Path) -> None:
  projectDir = tmp_path / "project"
  projectDir.mkdir(parents=True)
  (projectDir / "project.json").write_text(
    """{
      "schemaVersion": "2.0",
      "project": {
        "projectId": "p1",
        "name": "demo",
        "revision": 1,
        "createdAt": "2026-01-01T00:00:00Z",
        "updatedAt": "2026-01-01T00:00:00Z"
      },
      "entryWorkflowId": "main",
      "workflowOrder": ["main"],
      "workflows": {
        "main": {
          "name": "Main",
          "nodes": [
            {"nodeId": "input", "kind": "workflow_input"},
            {"nodeId": "output", "kind": "workflow_output"}
          ],
          "edges": []
        }
      },
      "dependencies": {"operators": ["vision.io.image_loader"]}
    }""",
    encoding="utf-8",
  )
  (projectDir / "plugins.lock").write_text("should be regenerated", encoding="utf-8")

  packagePath = buildPackage(projectDir=projectDir, outputDir=tmp_path)
  assert Path(packagePath).exists()
  assert str(packagePath).endswith(".vxpkg")

  with zipfile.ZipFile(packagePath, "r") as archive:
    names = set(archive.namelist())
    assert "manifest.json" in names
    assert "project.json" in names
    assert "plugins.lock" in names
    assert "project.yaml" not in names


def testBuildPackageArchivesCanonicalV2ForLegacyInput(tmp_path: Path) -> None:
  projectDir = tmp_path / "legacy"
  projectDir.mkdir(parents=True)
  (projectDir / "project.json").write_text(
    json.dumps(
      {
        "version": "1.0",
        "meta": {"projectId": "legacy-id", "name": "Legacy"},
        "designer": {"nodes": [], "edges": []},
      }
    ),
    encoding="utf-8",
  )

  packagePath = buildPackage(projectDir=projectDir, outputDir=tmp_path)

  with zipfile.ZipFile(packagePath, "r") as archive:
    projectPayload = json.loads(archive.read("project.json"))
    manifest = json.loads(archive.read("manifest.json"))
    assert projectPayload["schemaVersion"] == "2.1"
    assert manifest["schemaVersion"] == "2.0"
    nodes = projectPayload["workflows"]["main"]["nodes"]
    assert {node["kind"] for node in nodes} == {
      "workflow_input",
      "workflow_output",
    }
    assert manifest["checksum"]["project.json"] == hashlib.sha256(
      archive.read("project.json")
    ).hexdigest()
