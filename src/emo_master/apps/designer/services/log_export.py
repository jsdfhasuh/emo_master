"""Export the log window's captured records, including their full diagnostics."""
from datetime import datetime
import json
import os
from pathlib import Path
import tempfile


def exportLogs(path: Path, records: list[dict], *, formatName: str, scope: str,
               filters: dict, retainedCount: int) -> None:
    if formatName not in {"json", "jsonl", "txt"}:
        raise ValueError("不支持的日志导出格式：" + formatName)
    exportedAt = datetime.now().astimezone().isoformat(timespec="seconds")
    metadata = dict(schemaVersion="1.0", exportedAt=exportedAt, scope=scope,
                    filters=filters, retainedCount=retainedCount, entryCount=len(records))
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                         dir=path.parent, prefix=".emo-log-", suffix=".tmp",
                                         delete=False) as stream:
            temporary = Path(stream.name)
            if formatName == "json":
                json.dump(dict(metadata, entries=records), stream, ensure_ascii=False, indent=2, default=str)
                stream.write("\n")
            elif formatName == "jsonl":
                for record in records:
                    stream.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
            else:
                label = "当前筛选结果" if scope == "filtered" else "全部缓存日志"
                stream.write(f"emo_master 日志导出\n导出时间：{exportedAt}\n范围：{label}\n"
                             f"导出条数：{len(records)} / 缓存条数：{retainedCount}\n")
                stream.write("筛选条件：" + json.dumps(filters, ensure_ascii=False, default=str) + "\n")
                for record in records:
                    stamp = datetime.fromtimestamp(record["timestampMs"] / 1000).astimezone().isoformat(timespec="milliseconds")
                    stream.write(f"\n[{stamp}][{record['level']}] {record['message']}\n")
                    stream.write(json.dumps(record, ensure_ascii=False, indent=2, default=str) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
