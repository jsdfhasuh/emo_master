from pathlib import Path

from emo_master.core.project.repository import ProjectRepository


def testProjectYamlRoundTrip(tmp_path: Path) -> None:
  repo = ProjectRepository(tmp_path)
  projectId = repo.createProject("demo")
  loaded = repo.loadProject(projectId)

  assert loaded["name"] == "demo"
  assert loaded["projectId"] == projectId
