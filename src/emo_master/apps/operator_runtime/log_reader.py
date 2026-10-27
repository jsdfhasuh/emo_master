"""Bounded read-only JSONL tailing and display redaction, with no Qt dependency."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re


TAIL_BYTES = 2 * 1024 * 1024
TAIL_RECORDS = 2000
READ_BYTES = 256 * 1024
MAX_RECORDS = 10000
MAX_LINE_BYTES = 256 * 1024
SENSITIVE_PARTS = ("password", "secret", "token", "authorization", "credential", "apikey", "privatekey")
_URL_USER = re.compile(r"((?:https?|rtsp)://)[^\s/<>\"?#]*@", re.IGNORECASE)
_SECRET_VALUE = re.compile(
    r"((?:password|secret|token|authorization|credential|api[_-]?key|private[_-]?key)[\"']?\s*[=:]\s*)"
    r"(?:\"[^\"]*\"|'[^']*'|[^\s&,;<>]+)",
    re.IGNORECASE,
)
_AUTH = re.compile(r"\b(Bearer|Basic)\s+[\w.\-+/=]+", re.IGNORECASE)


def safeText(value) -> str:
    text = _URL_USER.sub(r"\1<redacted>@", str(value))
    return _SECRET_VALUE.sub(r"\1<redacted>", _AUTH.sub(r"\1 <redacted>", text))


def safeRecord(value, depth=0):
    if depth >= 16:
        return "<max-depth>"
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            normalized = "".join(char for char in str(key).casefold() if char.isalnum())
            result[safeText(key)] = ("<redacted>" if any(part in normalized for part in SENSITIVE_PARTS)
                                     else safeRecord(item, depth + 1))
        return result
    if isinstance(value, list):
        return [safeRecord(item, depth + 1) for item in value]
    return safeText(value) if isinstance(value, str) else value


@dataclass(frozen=True)
class LogRecord:
    path: Path
    offset: int
    data: dict

    @property
    def identity(self):
        return str(self.path), self.offset


@dataclass(frozen=True)
class ReadCursor:
    path: Path
    offset: int
    pending: bytes = b""
    fileId: tuple = ()
    skippingLine: bool = False


@dataclass(frozen=True)
class ReadResult:
    cursor: ReadCursor
    records: tuple[LogRecord, ...]
    reset: bool = False
    errors: int = 0
    reason: str = ""
    recentOnly: bool = False


class JsonlTailReader:
    def read(self, path: Path, cursor: ReadCursor | None = None) -> ReadResult:
        """One bounded operation. Call on a worker, not the GUI thread."""
        path = Path(path)
        with path.open("rb") as handle:
            import os
            stat = os.fstat(handle.fileno())
            fileId = stat.st_dev, stat.st_ino
            if cursor is None or cursor.path != path or cursor.fileId != fileId or stat.st_size < cursor.offset:
                reset = True
                start, pending, skipping = max(0, stat.st_size - TAIL_BYTES), b"", False
            else:
                reset = False
                start, pending, skipping = cursor.offset, cursor.pending, cursor.skippingLine
            recentOnly = reset and start > 0
            if reset and start:
                handle.seek(start - 1)
                skipping = handle.read(1) != b"\n"
            handle.seek(start)
            chunk = handle.read(TAIL_BYTES if reset else READ_BYTES) if reset or b"\n" not in pending else b""
        offset = start + len(chunk)
        buffer = pending + chunk
        position = start - len(pending)
        if reset:
            # Limit lines before JSON decoding, not after allocating every
            # record in the byte window (many tiny records can fit in 2 MiB).
            end, boundaries = len(buffer), 0
            while boundaries <= TAIL_RECORDS:
                boundary = buffer.rfind(b"\n", 0, end)
                if boundary < 0:
                    break
                end = boundary
                boundaries += 1
            if boundaries > TAIL_RECORDS:
                position += end + 1
                buffer = buffer[end + 1:]
                recentOnly, skipping = True, False
        records = []
        errors, reason = 0, ""
        lines = 0
        while b"\n" in buffer and lines < TAIL_RECORDS:
            lines += 1
            raw, buffer = buffer.split(b"\n", 1)
            lineStart = position
            position += len(raw) + 1
            if skipping:
                skipping = False
                continue
            try:
                if len(raw) > MAX_LINE_BYTES:
                    raise ValueError("单条记录超过 256 KiB，已跳过")
                if not raw.strip():
                    continue
                value = json.loads(raw.decode("utf-8"))
                if not isinstance(value, dict):
                    raise ValueError("JSONL 记录不是对象")
                records.append(LogRecord(path, lineStart, safeRecord(value)))
            except (UnicodeError, ValueError, RecursionError) as error:
                errors += 1
                # JSONDecodeError.str includes source fragments; do not echo them.
                reason = ("无效 UTF-8 / JSON 记录" if isinstance(error, (UnicodeError, json.JSONDecodeError))
                          else safeText(error))
        if len(buffer) > MAX_LINE_BYTES and b"\n" not in buffer:
            buffer = b""
            if not skipping:
                errors += 1
                reason = "单条记录超过 256 KiB，已跳过"
            skipping = True
        return ReadResult(ReadCursor(path, offset, buffer, fileId, skipping), tuple(records), reset,
                          errors, reason, recentOnly)


def listLogFiles(directory: Path) -> tuple[Path, ...]:
    if not directory.exists():
        return ()
    # Metadata/file reads stay on the same single background task.
    return tuple(sorted((path for path in directory.glob("runtime-*.jsonl") if path.is_file()),
                        key=lambda path: (path.stat().st_mtime_ns, path.name), reverse=True))


def recordSource(data):
    payload = data.get("payload")
    payload = payload if isinstance(payload, dict) else {}
    # Only names frozen into this record; never map historical IDs through a current project.
    return str(data.get("nodeName") or payload.get("nodeName") or data.get("workflowName")
               or payload.get("workflowName") or payload.get("deviceName")
               or data.get("nodeId") or data.get("workflowId") or data.get("eventType") or "Runtime")


def recordDetails(record: LogRecord) -> str:
    data = record.data
    payload = data.get("payload") if isinstance(data.get("payload"), dict) else {}
    def identity(kind):
        name = data.get(kind + "Name") or payload.get(kind + "Name")
        key = data.get(kind + "Id")
        return f"{name}（{key}）" if name and key else name or key
    fields = (("时间", data.get("timestamp") or data.get("timestampMs")), ("级别", data.get("level")),
              ("来源", recordSource(data)), ("事件", data.get("eventType")), ("错误码", data.get("code")),
              ("原始消息", data.get("message")), ("工程 / ID", identity("project")),
              ("流程 / ID", identity("workflow")), ("节点 / ID", identity("node")),
              ("Job ID", data.get("jobId")), ("流程运行 ID", data.get("workflowRunId")),
              ("节点运行 ID", data.get("nodeRunId")))
    lines = [f"{name}：{value}" for name, value in fields if value is not None and value != ""]
    lines.append(f"文件：{safeText(record.path)}\n字节偏移：{record.offset}")
    lines.append("payload（已脱敏）：\n" + json.dumps(data.get("payload", {}), ensure_ascii=False, indent=2))
    return "\n".join(lines)
