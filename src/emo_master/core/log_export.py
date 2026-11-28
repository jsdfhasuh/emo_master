"""Sequential log export shared by desktop applications, with atomic completion."""
from collections.abc import Callable, Iterable
from datetime import datetime
import json
import os
from pathlib import Path
import re
import tempfile

from emo_master.core.runtime_log_messages import runtimeEventMessage, runtimeLogBrief


_MARKDOWN_SPECIAL = re.compile(r"([\\`*_\[\]<>#|&~])")


def _markdownText(value: object) -> str:
    return _MARKDOWN_SPECIAL.sub(r"\\\1", " ".join(str(value).splitlines()).strip())


def _markdownTimelineRow(record: dict) -> str:
    payload = record.get("payload")
    payload = payload if isinstance(payload, dict) else {}
    timestamp = record.get("timestampMs")
    stamp = record.get("timestamp")
    if type(timestamp) is int and timestamp > 0:
        try:
            stamp = datetime.fromtimestamp(timestamp / 1000).isoformat(sep=" ", timespec="milliseconds")
        except (ValueError, OverflowError, OSError):
            stamp = stamp or timestamp
    context = [payload.get(kind + "Name") or record.get(kind + "Name") or record.get(kind + "Id")
               for kind in ("workflow", "node")]
    target = payload.get("targetWorkflowName") or payload.get("targetWorkflowId")
    if target:
        context.append(f"子工作流 {target}")
    iteration = record.get("iterationPath")
    if isinstance(iteration, (list, tuple)) and iteration and all(type(i) is int and i >= 0 for i in iteration):
        context.append("第 " + " / ".join(str(i + 1) for i in iteration) + " 轮")
    eventId = record.get("eventId")
    values = (stamp, f"事件 {eventId}" if eventId is not None else None, record.get("level"),
              record.get("eventType"), *context, record.get("code"), runtimeEventMessage(record), runtimeLogBrief(record))
    return "- " + " · ".join(_markdownText(value) for value in values if value not in (None, "")) + "\n"


class LogExportCancelled(Exception):
    """The selected export was cancelled before replacing the destination."""


def exportLogs(path: Path, records: Iterable[dict], *, formatName: str, scope: str,
               filters: dict, retainedCount: int | None = None,
               cancelled: Callable[[], bool] = lambda: False) -> int:
    if formatName not in {"json", "jsonl", "txt", "md"}:
        raise ValueError("不支持的日志导出格式：" + formatName)
    path = Path(path)
    exportedAt = datetime.now().astimezone().isoformat(timespec="seconds")
    metadata: dict[str, object] = dict(schemaVersion="1.0", exportedAt=exportedAt, scope=scope, filters=filters)
    if retainedCount is not None:
        metadata["retainedCount"] = retainedCount
    labels = dict(filtered="当前显示筛选记录", all_cached="全部缓存日志",
                  runtime_history="Runtime 历史（固定筛选与上界）", local_files="选定本机文件范围",
                  device_cache="已返回的设备诊断缓存", filtered_job="当前 Job 已加载且匹配的记录",
                  whole_job="完整 Job（固定上界内仍可查询的记录）")
    count, temporary = 0, None
    def checkCancelled():
        if cancelled():
            raise LogExportCancelled("日志导出已取消")
    try:
        checkCancelled()
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                         dir=path.parent, prefix=".emo-log-", suffix=".tmp",
                                         delete=False) as stream:
            temporary = Path(stream.name)
            if formatName == "json":
                stream.write('{"entries":[')
            elif formatName == "txt":
                stream.write(f"emo_master 日志导出\n导出时间：{exportedAt}\n范围：{labels.get(scope, scope)}\n")
                stream.write("筛选条件：" + json.dumps(filters, ensure_ascii=False, default=str) + "\n")
            elif formatName == "md":
                stream.write(f"# Job 日志\n\n范围 · {_markdownText(labels.get(scope, scope))}\n\n")
                for key, label in (("jobId", "Job"), ("projectId", "工程"), ("upperEventId", "记录上界")):
                    if filters.get(key) not in (None, ""):
                        stream.write(f"- {label} · {_markdownText(filters[key])}\n")
                stream.write("\n## 时间线\n\n")
            for record in records:
                checkCancelled()
                if formatName == "json":
                    stream.write(",\n" if count else "\n")
                    json.dump(record, stream, ensure_ascii=False, default=str)
                elif formatName == "jsonl":
                    stream.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
                elif formatName == "md":
                    stream.write(_markdownTimelineRow(record))
                else:
                    stamp = record.get("timestamp") or record.get("timestampMs", "")
                    stream.write(f"\n[{stamp}][{record.get('level', '')}] {record.get('message', '')}\n")
                    stream.write(json.dumps(record, ensure_ascii=False, indent=2, default=str) + "\n")
                count += 1
            checkCancelled()
            if formatName == "json":
                stream.write("\n]")
                for key, value in dict(metadata, entryCount=count).items():
                    stream.write(",\n" + json.dumps(key) + ": ")
                    json.dump(value, stream, ensure_ascii=False, default=str)
                stream.write("}\n")
            elif formatName == "txt":
                stream.write(f"\n导出条数：{count}")
                if retainedCount is not None:
                    stream.write(f" / 缓存条数：{retainedCount}")
                stream.write("\n")
            elif formatName == "md":
                stream.write(f"\n导出条数 · {count}\n")
            stream.flush()
            os.fsync(stream.fileno())
        checkCancelled()
        os.replace(temporary, path)
        return count
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
