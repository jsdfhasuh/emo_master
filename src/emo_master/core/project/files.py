"""Resolve user-named project documents without guessing between projects."""
from pathlib import Path


PROJECT_SUFFIX = ".emoproj"
LEGACY_PROJECT_NAME = "project.json"
PROJECT_OPEN_FILTER = (
    "EMO Master \u9879\u76ee (*.emoproj project.json);;"
    "EMO Master \u9879\u76ee (*.emoproj);;\u65e7\u7248\u9879\u76ee (project.json)"
)
PROJECT_SAVE_FILTER = "EMO Master \u9879\u76ee (*.emoproj)"


def isProjectFile(path: Path) -> bool:
    return path.suffix.lower() == PROJECT_SUFFIX or path.name.lower() == LEGACY_PROJECT_NAME


def projectFiles(directory: Path) -> list[Path]:
    return sorted(
        (path for path in directory.iterdir() if path.is_file() and isProjectFile(path)),
        key=lambda path: path.name.casefold(),
    )


def resolveProjectFile(path: Path) -> Path:
    if path.is_file() and isProjectFile(path):
        return path
    if path.is_dir():
        candidates = projectFiles(path)
        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            raise ValueError("multiple project files; select the exact .emoproj or project.json file")
    raise ValueError(f"project file not found; select a .emoproj or project.json file: {path}")


def projectFileForSave(path: Path) -> Path:
    if not path.is_dir() and isProjectFile(path):
        return path
    if path.is_file():
        raise ValueError("unsupported project filename; use .emoproj or project.json")
    if path.is_dir() and projectFiles(path):
        return resolveProjectFile(path)
    # Directory-based APIs keep their legacy default; the UI supplies a filename.
    return path / LEGACY_PROJECT_NAME
