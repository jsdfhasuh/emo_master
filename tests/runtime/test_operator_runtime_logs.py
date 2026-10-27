import json

from emo_master.apps.operator_runtime.log_reader import JsonlTailReader, MAX_LINE_BYTES, TAIL_RECORDS, safeRecord, safeText
from emo_master.apps.runtime.events.jsonl_writer import RuntimeJsonlLogWriter
from emo_master.apps.runtime.events.models import RuntimeEvent


def line(message="same", level="INFO", **fields):
    return (json.dumps(dict(timestamp="2026-10-25T02:03:04+00:00", level=level, message=message,
                           code="E_SYNTHETIC", **fields), ensure_ascii=False) + "\n").encode("utf-8")


def testTailAppendPartialLineInvalidJsonAndDuplicateMessages(tmp_path):
    path = tmp_path / "runtime-synthetic.jsonl"
    encoded = line("中文 🙂")
    path.write_bytes(line() + encoded[:-3])
    reader = JsonlTailReader()
    first = reader.read(path)
    assert [row.data["message"] for row in first.records] == ["same"]
    assert first.cursor.pending
    with path.open("ab") as handle:
        handle.write(encoded[-3:] + b"not json\n" + line() + line())
    second = reader.read(path, first.cursor)
    assert [row.data["message"] for row in second.records] == ["中文 🙂", "same", "same"]
    assert len({row.identity for row in second.records}) == 3
    assert second.errors == 1 and "JSON" in second.reason
    assert reader.read(path, second.cursor).records == ()


def testInitialReadIsTailWindowAndSubsequentReadsAreBounded(tmp_path):
    path = tmp_path / "runtime-large.jsonl"
    path.write_bytes(b"".join(line(str(index)) for index in range(25000)))
    reader = JsonlTailReader()
    initial = reader.read(path)
    assert len(initial.records) == TAIL_RECORDS
    assert initial.recentOnly and initial.records[-1].data["message"] == "24999"
    with path.open("ab") as handle:
        handle.write(b"".join(line(str(index)) for index in range(5000)))
    result = reader.read(path, initial.cursor)
    assert result.cursor.offset - initial.cursor.offset <= 256 * 1024
    assert len(result.records) <= TAIL_RECORDS
    allRecords = list(result.records)
    for _ in range(10):
        nextResult = reader.read(path, result.cursor)
        allRecords.extend(nextResult.records)
        if nextResult.cursor == result.cursor:
            break
        result = nextResult
    assert len(allRecords) == 5000
    assert len({row.identity for row in allRecords}) == 5000


def testOversizedPartialAndTruncationHaveNormalRecovery(tmp_path):
    path = tmp_path / "runtime-long.jsonl"
    path.write_bytes(b"x" * (MAX_LINE_BYTES + 1))
    reader = JsonlTailReader()
    result = reader.read(path)
    assert result.errors == 1 and not result.cursor.pending
    with path.open("ab") as handle:
        handle.write(b"end\n" + line("recovered"))
    result = reader.read(path, result.cursor)
    assert [row.data["message"] for row in result.records] == ["recovered"]
    path.write_bytes(line("truncated"))
    result = reader.read(path, result.cursor)
    assert result.reset and result.records[0].data["message"] == "truncated"


def testDisplayRedactsSensitiveFieldsAndUrlUserInfoWithoutLosingReasons():
    raw = {"message": "Cannot open rtsp://admin:fake-secret@camera.invalid/a?token=fake-token; E_CAMERA_CONNECT",
           "payload": {"Password": "private", "headers": {"Authorization": "Bearer fake-key"},
                       "cause": "HTTP http://user:pass@example.invalid/a", "nodeId": "capture"}}
    safe = safeRecord(raw)
    encoded = json.dumps(safe)
    for value in ("admin", "fake-secret", "fake-token", "private", "fake-key", "user:pass"):
        assert value not in encoded
    assert "E_CAMERA_CONNECT" in encoded and "capture" in encoded


def testOnlyExplicitStartupEventsAreAddedToWriterAndNeverJobDatabase(tmp_path):
    writer = RuntimeJsonlLogWriter(tmp_path)
    try:
        assert not writer.enqueue(RuntimeEvent(jobId="", eventType="operator.arbitrary", message="not allowlisted"))
        for eventType in ("operator.storage.resolved", "operator.runtime.ready", "operator.runtime.failed", "operator.project.load.failed"):
            assert writer.enqueue(RuntimeEvent(jobId="", eventType=eventType, message="synthetic", timestampMs=1792893600000))
    finally:
        assert writer.close() is None
    records = [json.loads(row) for path in tmp_path.glob("runtime-*.jsonl") for row in path.read_text().splitlines()]
    assert len(records) == 4 and all(not row["jobId"] for row in records)


def testFreeformQuotedSecretsAndHttpAuthorizationAreRedacted():
    for raw, secret in (("Authorization: Bearer synthetic-bearer", "synthetic-bearer"),
                        ("authorization=Basic c3ludGhldGlj", "c3ludGhldGlj"),
                        ('password="synthetic with spaces"', "synthetic with spaces"),
                        ('{"token":"synthetic-token"}', "synthetic-token")):
        assert secret not in safeText(raw)
        assert "<redacted>" in safeText(raw)


def testLineBudgetAlsoBoundsTinyAndMalformedRecords(tmp_path):
    path = tmp_path / "runtime-tiny.jsonl"
    path.write_bytes(b"{}\n" * 10000)
    reader = JsonlTailReader()
    first = reader.read(path)
    assert len(first.records) == TAIL_RECORDS and first.recentOnly
    assert first.records[0].offset == 8000 * 3
    with path.open("ab") as handle:
        handle.write(b"invalid\n" * 10000)
    second = reader.read(path, first.cursor)
    assert second.errors == TAIL_RECORDS and second.cursor.pending
    third = reader.read(path, second.cursor)
    assert third.errors == TAIL_RECORDS
    assert third.cursor.offset == second.cursor.offset  # Consume the bounded remainder first.
