"""Exact staging-file ownership shared by a worker and its retiring parent."""
from contextlib import contextmanager, nullcontext
import json
import os
from pathlib import Path
from typing import BinaryIO, Iterator
from uuid import uuid4

from emo_master.apps.runtime.execution.staging_registry import (
    ImageStagingRegistry, checkStagingPath, recoverTemporaryFiles as recoverTemporaryFiles,
)
from emo_master.apps.runtime.preview.store import _ioPath


def _stagingPath(directory: Path, key: str) -> Path:
    return _ioPath(directory / f".emo-image-{key}.tmp")


@contextmanager
def temporaryFile(directory: str | Path, workspace: str | None,
                  recoveryRoot: str | Path | ImageStagingRegistry | None = None) -> Iterator[BinaryIO]:
    output = Path(directory).resolve()
    registry = (ImageStagingRegistry(recoveryRoot) if isinstance(recoveryRoot, (Path, str)) else recoveryRoot)
    location = registry.directory(output) if registry is not None else nullcontext(output)
    with location as directoryPath, _ownedFile(directoryPath, workspace, registered=registry is not None) as stream:
        yield stream


@contextmanager
def _ownedFile(directory: Path, workspace: str | None, *, registered: bool) -> Iterator[BinaryIO]:
    key = uuid4().hex
    path = _stagingPath(directory, key)
    if os.path.lexists(path):
        raise FileExistsError(path)
    registration = None
    if workspace:
        owner = _ioPath(Path(workspace) / ".background-image-files")
        checkStagingPath(owner)
        owner.mkdir(mode=0o700, parents=True, exist_ok=True)
        registration = owner / key
        # A complete registration is visible before the output file can exist.
        # Distinct registrations allow a previous debug writer to still be exiting.
        with registration.open("x", encoding="utf-8") as record:
            json.dump({"version": 2, "directory": str(directory)} if registered else str(directory), record)
    try:
        stream = open(path, "xb", opener=lambda name, flags: os.open(name, flags, 0o600))
    except BaseException:
        if registration is not None:
            registration.unlink(missing_ok=True)
        raise
    try:
        with stream:
            yield stream
    finally:
        path.unlink(missing_ok=True)
        if registration is not None:
            registration.unlink(missing_ok=True)


def retireTemporaryFiles(workspace: str | None, *, legacyOnly: bool = False) -> None:
    """Only call after the owning process has exited."""
    if not workspace:
        return
    owner = _ioPath(Path(workspace) / ".background-image-files")
    checkStagingPath(owner)
    if not owner.is_dir():
        return
    for record in owner.iterdir():
        key = record.name
        if len(key) != 32 or any(char not in "0123456789abcdef" for char in key):
            continue
        checkStagingPath(record)
        try:
            directory = json.loads(record.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            # The process died while registering. No staging file was opened.
            directory = None
        if isinstance(directory, dict) and directory.get("version") == 2:
            if legacyOnly:
                continue
            directory = directory.get("directory")
        if isinstance(directory, str) and Path(directory).is_absolute():
            location = Path(directory)
            if location != location.resolve() or (legacyOnly and not location.is_dir()):
                raise OSError(f"registered staging directory unavailable: {location}")
            _stagingPath(location, key).unlink(missing_ok=True)
        record.unlink()
    if not any(owner.iterdir()):
        owner.rmdir()
