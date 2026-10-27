"""Per-launch Designer choices and local preferences, without Runtime imports."""
from __future__ import annotations

from dataclasses import dataclass, replace
import os
from pathlib import Path
from typing import Mapping


RECENT_LIMIT = 10
DEFAULT_RUNTIME_TARGET = "127.0.0.1:50051"


@dataclass(frozen=True)
class DesignerStartupOptions:
    mode: str
    dataRoot: Path
    dbPath: Path
    workspaceRoot: Path
    logDirectory: Path
    runtimeTarget: str = DEFAULT_RUNTIME_TARGET

    def withDataRoot(self, directory: Path) -> DesignerStartupOptions:
        root = directory.expanduser().resolve()
        return replace(self, mode="local", dataRoot=root, dbPath=root / "emo_master.db",
                       workspaceRoot=root / "jobs", logDirectory=root / "logs")


def defaultDataRoot() -> Path:
    return Path.home() / ".emo_master" / "runtime"


def recentValues(preferences, key: str) -> list[str]:
    values = preferences.value("startup/" + key, [])
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, (list, tuple)):
        return []
    return [value for value in values if isinstance(value, str) and value.strip()][:RECENT_LIMIT]


def initialStartupOptions(preferences, *, environment: Mapping[str, str] | None = None,
                          cwd: Path | None = None) -> DesignerStartupOptions:
    env = os.environ if environment is None else environment
    base = Path.cwd() if cwd is None else cwd

    def absolute(value) -> Path:
        path = Path(value).expanduser()
        return (path if path.is_absolute() else base / path).resolve()

    root = absolute(env.get("EMO_RUNTIME_DATA_DIR") or preferences.value("startup/dataRoot", "")
                    or defaultDataRoot())
    db = env.get("EMO_RUNTIME_DB_PATH") or env.get("EMO_MASTER_RUNTIME_DB_PATH")
    target = env.get("EMO_RUNTIME_TARGET", "").strip()
    localEnvironment = any(env.get(key) for key in (
        "EMO_RUNTIME_DATA_DIR", "EMO_RUNTIME_DB_PATH", "EMO_MASTER_RUNTIME_DB_PATH", "EMO_RUNTIME_LOG_DIR"))
    mode = "remote" if target else ("local" if localEnvironment else preferences.value("startup/mode", "local"))
    return DesignerStartupOptions(
        mode="remote" if mode == "remote" else "local", dataRoot=root,
        dbPath=absolute(db) if db else root / "emo_master.db", workspaceRoot=root / "jobs",
        logDirectory=absolute(env["EMO_RUNTIME_LOG_DIR"]) if env.get("EMO_RUNTIME_LOG_DIR") else root / "logs",
        runtimeTarget=target or str(preferences.value("startup/runtimeTarget", "") or DEFAULT_RUNTIME_TARGET),
    )


def _recentWith(value: str, values: list[str], *, paths: bool) -> list[str]:
    result, seen = [], set()
    for item in [value, *values]:
        key = os.path.normcase(str(Path(item).expanduser().resolve())) if paths else item.casefold()
        if key not in seen:
            result.append(item)
            seen.add(key)
    return result[:RECENT_LIMIT]


def saveStartupOptions(preferences, options: DesignerStartupOptions) -> None:
    from PySide2.QtCore import QSettings

    # Use a fresh handle because QSettings.status() retains its first error.
    store = QSettings(preferences.fileName(), preferences.format())
    store.setFallbacksEnabled(False)
    changes: dict[str, object] = {"startup/mode": options.mode}
    if options.mode == "local":
        root = str(options.dataRoot)
        changes.update({"startup/dataRoot": root,
                        "startup/recentDataRoots": _recentWith(root, recentValues(store, "recentDataRoots"), paths=True)})
    else:
        target = options.runtimeTarget
        changes.update({"startup/runtimeTarget": target,
                        "startup/recentRuntimeTargets": _recentWith(target, recentValues(store, "recentRuntimeTargets"), paths=False)})
    previous = {key: store.value(key) if store.contains(key) else None for key in changes}
    for key, value in changes.items():
        store.setValue(key, value)
    store.sync()
    if store.status() != QSettings.NoError:
        for key, savedValue in previous.items():
            if savedValue is None:
                store.remove(key)
            else:
                store.setValue(key, savedValue)
        store.sync()
        raise OSError(f"无法保存本机启动设置：{store.fileName()}")
