"""Local operator storage policy. No Qt, directory creation, or project mutation."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Mapping


DEFAULT_ROOT_KEY = "storage/defaultDataRoot"
SOURCE_LABELS = {
    "explicit": "启动参数 --data-root / 显式目录",
    "environment": "环境变量 EMO_RUNTIME_DATA_DIR",
    "saved": "本机保存的默认目录",
    "system": "系统默认目录",
}
UNUSED_VARIABLES = ("EMO_RUNTIME_DB_PATH", "EMO_MASTER_RUNTIME_DB_PATH", "EMO_RUNTIME_LOG_DIR")


def systemDataRoot(environment: Mapping[str, str] | None = None) -> Path:
    env = os.environ if environment is None else environment
    return Path(env.get("LOCALAPPDATA") or Path.home() / ".local/share") / "EmoMaster" / "operator-runtime"


@dataclass(frozen=True)
class DataRootResolution:
    path: Path
    source: str
    systemDefault: Path
    ignoredVariables: tuple[str, ...] = ()

    @property
    def sourceLabel(self) -> str:
        return SOURCE_LABELS[self.source]

    @property
    def overridden(self) -> bool:
        return self.source in {"explicit", "environment"}


def resolveDataRoot(explicit=None, *, environment=None, saved=None, platformDefault=None,
                    cwd: Path | None = None) -> DataRootResolution:
    env = os.environ if environment is None else environment
    base = Path.cwd() if cwd is None else Path(cwd)

    def absolute(value):
        path = Path(value).expanduser()
        return (path if path.is_absolute() else base / path).resolve()

    default = absolute(platformDefault if platformDefault is not None else systemDataRoot(env))
    candidates = (("explicit", explicit), ("environment", env.get("EMO_RUNTIME_DATA_DIR")),
                  ("saved", saved), ("system", default))
    source, value = next((source, value) for source, value in candidates if value is not None and str(value) != "")
    return DataRootResolution(absolute(value), source, default, tuple(key for key in UNUSED_VARIABLES if env.get(key)))


def validateSavedDataRoot(value: str) -> Path:
    """A preference is an absolute directory; CLI relative paths remain supported."""
    if not value or not value.strip():
        raise ValueError("请输入绝对目录，或点击“使用系统默认”。")
    if any(ord(char) < 32 for char in value):
        raise ValueError("目录包含无效控制字符。")
    path = Path(value)
    if not path.is_absolute():
        raise ValueError("本机默认目录必须是绝对路径；不能使用相对路径或盘符相对路径。")
    if os.name == "nt":
        if any(char in '<>"|?*' for char in value) or ":" in value[len(path.drive):]:
            raise ValueError("目录包含 Windows 不支持的字符。")
        if any(part.rstrip(". ") != part or Path(part).is_reserved() for part in path.parts[1:]):
            raise ValueError("目录包含 Windows 保留名称或结尾空格/句点。")
    path = path.resolve()
    if path.exists() and not path.is_dir():
        raise ValueError("目标路径是文件，不是目录。")
    for parent in path.parents:
        if parent.exists():
            if not parent.is_dir():
                raise ValueError(f"上级路径是文件，不是目录：{parent}")
            break
    return path


def runtimePaths(root: Path, controller=None) -> dict[str, Path]:
    """Effective paths, using the live owners when initialized, never the draft."""
    runtime = controller.runtime if controller is not None else None
    jobs = Path(runtime.workspaceRoot) if runtime is not None else root / "jobs"
    return {
        "数据库": Path(runtime.sqliteStore.dbPath) if runtime is not None else root / "runtime.sqlite3",
        "日志": Path(runtime.operationalLogWriter.directory) if runtime is not None else root / "logs",
        "任务工作区": jobs,
        "托管工程根（不是当前工程/业务留档）": jobs.parent / "online-projects",
        "显示缓存": Path(controller.presentation.root) if controller is not None else root / "display",
    }
