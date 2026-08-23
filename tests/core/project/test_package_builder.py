from pathlib import Path
import zipfile

from emo_master.core.project.package_builder import buildPackage


def testBuildPackageCreatesArchive(tmp_path: Path) -> None:
  projectDir = tmp_path / "project"
  projectDir.mkdir(parents=True)
  (projectDir / "project.yaml").write_text("projectId: p1\nname: demo\n", encoding="utf-8")
  (projectDir / "plugins.lock").write_text("[]", encoding="utf-8")

  packagePath = buildPackage(projectDir=projectDir, outputDir=tmp_path)
  assert Path(packagePath).exists()
  assert str(packagePath).endswith(".vxpkg")

  with zipfile.ZipFile(packagePath, "r") as archive:
    names = set(archive.namelist())
    assert "manifest.json" in names
    assert "project.yaml" in names
