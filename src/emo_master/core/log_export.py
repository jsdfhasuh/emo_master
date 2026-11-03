"""Sequential log export shared by desktop applications, with atomic completion."""
from collections.abc import Callable, Iterable
from datetime import datetime
import json
import os
from pathlib import Path
import tempfile


class LogExportCancelled(Exception):
    """The selected export was cancelled before replacing the destination."""


def exportLogs(path: Path, records: Iterable[dict], *, formatName: str, scope: str,
               filters: dict, retainedCount: int | None = None,
               cancelled: Callable[[], bool] = lambda: False) -> int:
    if formatName not in {"json", "jsonl", "txt"}:
        raise ValueError("不支持的日志导出格式：" + formatName)
    path = Path(path)
    exportedAt = datetime.now().astimezone().isoformat(timespec="seconds")
    metadata: dict[str, object] = dict(schemaVersion="1.0", exportedAt=exportedAt, scope=scope, filters=filters)
    if retainedCount is not None:
        metadata["retainedCount"] = retainedCount
    labels = dict(filtered="当前显示筛选记录", all_cached="全部缓存日志",
                  runtime_history="Runtime 历史（固定筛选与上界）", local_files="选定本机文件范围",
                  device_cache="已返回的设备诊断缓存")
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
            for record in records:
                checkCancelled()
                if formatName == "json":
                    stream.write(",\n" if count else "\n")
                    json.dump(record, stream, ensure_ascii=False, default=str)
                elif formatName == "jsonl":
                    stream.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
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
            stream.flush()
            os.fsync(stream.fileno())
        checkCancelled()
        os.replace(temporary, path)
        return count
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
