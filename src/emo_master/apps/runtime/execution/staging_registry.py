"""Durable locations of Runtime-owned image staging directories."""
from contextlib import closing, contextmanager
import errno
import os
from pathlib import Path
import re
import sqlite3
import stat
import time
from uuid import uuid4

from emo_master.apps.runtime.preview.store import _ioPath
from emo_master.apps.runtime.project_config.resource_lease import ResourceLease


_KEY = re.compile(r"[0-9a-f]{32}")
_STAGED_IMAGE = re.compile(r"\.emo-image-[0-9a-f]{32}\.tmp")


def checkStagingPath(path: Path) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if (stat.S_ISLNK(info.st_mode)
            or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
        raise OSError(f"staging path is a link or reparse point: {path}")


def _stageDirectory(output: Path, key: str) -> Path:
    if not _KEY.fullmatch(key) or not output.is_absolute() or output.resolve() != output:
        raise ValueError(f"invalid image staging directory registration: {output}")
    checkStagingPath(output)
    stage = output / f".emo-image-stage-{key}"
    checkStagingPath(_ioPath(stage))
    return stage


def _database(path: Path) -> sqlite3.Connection:
    checkStagingPath(path)
    for suffix in ("-journal", "-wal", "-shm"):
        checkStagingPath(path.with_name(path.name + suffix))
    return sqlite3.connect(path, timeout=5)


def _registeredToken(database: Path, output: Path) -> str:
    # Bind rows to this data root. Restoring a copy elsewhere does not confer
    # ownership of another Runtime's output directories.
    scope = str(database.resolve())
    target = str(output)
    with closing(_database(database)) as db:
        db.execute("PRAGMA synchronous=EXTRA")
        db.execute("""CREATE TABLE IF NOT EXISTS imageStagingDirectories (
            registry TEXT NOT NULL, output TEXT NOT NULL, token TEXT NOT NULL,
            PRIMARY KEY (registry, output), UNIQUE(token))""")
        query = "SELECT token FROM imageStagingDirectories WHERE registry=? AND output=?"
        row = db.execute(query, (scope, target)).fetchone()
        if row:
            return row[0]
        # Two workers can encounter a new output folder at the same time.
        with db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(query, (scope, target)).fetchone()
            if row:
                return row[0]
            key = uuid4().hex
            stage = _stageDirectory(output, key)
            if os.path.lexists(_ioPath(stage)):
                raise FileExistsError(stage)
            db.execute("INSERT INTO imageStagingDirectories VALUES (?, ?, ?)", (scope, target, key))
    # The FULL-strength commit is complete before anything on the output
    # volume is created. Keep the row through subsequent writes and cleanups.
    return key


def _busy(error: OSError) -> bool:
    return (getattr(error, "winerror", None) in (32, 33)
            or (os.name != "nt" and error.errno in (errno.EAGAIN, errno.EWOULDBLOCK)))


class ImageStagingRegistry:
    def __init__(self, database: str | Path) -> None:
        self.database = Path(database)
        self._last: tuple[str, str] | None = None

    @contextmanager
    def directory(self, output: Path):
        location = self._last
        if location is None or location[0] != str(output):
            location = (str(output), _registeredToken(self.database, output))
            self._last = location
        stage = _stageDirectory(output, location[1])
        ioStage = _ioPath(stage)
        ioStage.mkdir(mode=0o700, exist_ok=True)
        guard = ioStage / ".owner.lock"
        checkStagingPath(guard)
        deadline = time.monotonic() + 5
        while True:
            try:
                lease = ResourceLease(guard, create=True)
                break
            except OSError as error:
                # A previous Runtime's last writer may briefly overlap a startup
                # sweep. Normal writers all hold compatible shared leases.
                if not _busy(error) or time.monotonic() >= deadline:
                    raise
                time.sleep(.02)
        with closing(lease):
            yield stage


def _sweep(stage: Path, errors: list[str]) -> None:
    stage = _ioPath(stage)
    if not stage.is_dir():
        return
    guard = stage / ".owner.lock"
    checkStagingPath(guard)
    # A reboot can leave the directory entry but lose its first lock file.
    # Materialize the same reusable lock before requesting exclusive access.
    if not guard.exists():
        ResourceLease(guard, create=True).close()
    try:
        lease = ResourceLease(guard, retiring=True)
    except OSError as error:
        if _busy(error):
            return
        raise
    with closing(lease):
        for child in stage.iterdir():
            if _STAGED_IMAGE.fullmatch(child.name):
                try:
                    checkStagingPath(child)
                    if child.is_file():
                        child.unlink(missing_ok=True)
                except OSError as error:
                    errors.append(f"{child}: {error}")


def recoverTemporaryFiles(recoveryRoot: str | Path) -> list[str]:
    """Sweep registered private directories, retaining unavailable paths for a later startup."""
    database = Path(recoveryRoot)
    if not os.path.lexists(database):
        return []
    errors: list[str] = []
    try:
        with closing(_database(database)) as db:
            if not db.execute("""SELECT 1 FROM sqlite_master WHERE type='table'
                    AND name='imageStagingDirectories'""").fetchone():
                return []
            rows = db.execute("SELECT output, token FROM imageStagingDirectories WHERE registry=?",
                              (str(database.resolve()),)).fetchall()
        for target, key in rows:
            try:
                _sweep(_stageDirectory(Path(target), key), errors)
            except (OSError, ValueError) as error:
                errors.append(f"{target}: {error}")
    except (OSError, sqlite3.Error) as error:
        errors.append(f"{database}: {error}")
    return errors
