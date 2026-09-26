"""Debug namespace materialization; explicitly opt-in, no legacy migration."""
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from emo_master.apps.runtime.context.global_counters import ProjectGlobalCounters
from emo_master.apps.runtime.context.sqlite_store import SqliteStore


@dataclass(frozen=True)
class DebugState:
    db: Path
    output: Path

    @classmethod
    def create(cls, temporary_project):
        root = Path(temporary_project) / "debug" / str(uuid4())
        root.mkdir(parents=True)
        output = root / "output"
        output.mkdir()
        return cls(root / "counters.db", output)

    def counters(self, project_id):
        store = SqliteStore(self.db)
        store.initialize()
        return ProjectGlobalCounters(store, project_id)

    def file(self, name):
        if Path(name).name != name or name in ("", ".", ".."):
            raise ValueError("output must be a local filename")
        path = self.output / name
        if path.is_symlink() or path.resolve().parent != self.output.resolve():
            raise ValueError("output escape")
        return path
