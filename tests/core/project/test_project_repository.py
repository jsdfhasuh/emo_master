from pathlib import Path

from emo_master.core.project.repository import ProjectRepository


def testProjectJsonV2RoundTrip(tmp_path: Path) -> None:
  repo = ProjectRepository(tmp_path)
  projectId = repo.createProject("demo")
  loaded = repo.loadProject(projectId)

  assert loaded["name"] == "demo"
  assert loaded["projectId"] == projectId
  assert loaded["schemaVersion"] == "2.0"
  kinds = {
    node["kind"] for node in loaded["workflows"]["main"]["nodes"]
  }
  assert kinds == {"workflow_input", "workflow_output"}
  document = repo.loadProjectDocument(projectId)
  assert document.project.projectId == projectId
