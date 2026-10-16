"""Real saved images and SQLite metadata at Windows/Unicode boundaries."""
import os
import sqlite3
import subprocess

import cv2
import numpy as np
import pytest

from emo_master.apps.runtime.business_sqlite.backend import initialize, inspect, resolveTarget, validateMappings
from emo_master.apps.runtime.workflow.cancellation import CancellationToken
from emo_master.apps.runtime.workflow.context import RunContext
from emo_master.apps.runtime.workflow.runner import WorkflowExecutionError, WorkflowRunner
from emo_master.core.contracts.sqlite_writer import OPERATOR_ID, SqliteWriterError, parseConfig, quoteIdentifier, toStorage
from emo_master.core.workflow.compiler import WorkflowCompiler
from emo_master.plugins.builtins.image_saver.operator import ImageSaverOperator
from emo_master.plugins.builtins.sqlite_writer.operator import SqliteWriterOperator
from tests.sqlite_writer.test_backend import actualRun
from tests.sqlite_writer.test_dependencies import config, mapping, project


@pytest.mark.parametrize("directory", ["preview-cache", "PREVIEW-CACHE", "PREVIEW_STAGING", "JOBS", "持久 图片"])
@pytest.mark.parametrize("policy", ["stop", "continue"])
def testRealImageSaverBindingRejectsTransientPathsAndKeepsPersistentReference(tmp_path, directory, policy):
    if os.name != "nt" and directory.isupper():
        pytest.skip("Windows case-insensitive filesystem regression")
    imagePath = tmp_path / directory / "真实 图像.png"
    path = tmp_path / "business.sqlite3"
    initialize(path, "records", [{"name": "value", "storageType": "FILE_REFERENCE"}])
    payload = project(nodes=[
        {"nodeId": "saver", "operatorId": "vision.io.image_saver", "params": {"outputPath": str(imagePath)}},
        {"nodeId": "writer", "operatorId": OPERATOR_ID,
         "params": config(path, [mapping(node="saver", port="result", storage="FILE_REFERENCE")], policy)}],
        edges=[{"fromNode": "input", "fromPort": "image", "toNode": "saver", "toPort": "image"}])
    payload["workflows"]["main"]["inputs"] = {"image": "image"}
    operators = {OPERATOR_ID: SqliteWriterOperator, "vision.io.image_saver": ImageSaverOperator}
    events = []
    runner = WorkflowRunner(WorkflowCompiler(operators).compile(payload), operators, eventPublisher=lambda **row: events.append(row))
    def execute():
        return runner.run("main", {"image": np.full((8, 8, 3), 17, np.uint8)},
                          RunContext.root("image-job", "main"), CancellationToken())
    persistent = directory == "持久 图片"
    if not persistent and policy == "stop":
        with pytest.raises(WorkflowExecutionError) as failed:
            execute()
        assert failed.value.code == "E_SQLITE_VALUE"
        receipt = failed.value.diagnostics["sqliteReceipt"]
    else:
        execute()
        receipt = next(row["payload"]["outputs"]["receipt"] for row in events
                       if row["eventType"] == "node.completed" and row["context"].callerNodeId == "writer")
    assert cv2.imdecode(np.frombuffer(imagePath.read_bytes(), np.uint8), cv2.IMREAD_COLOR).shape == (8, 8, 3)
    with sqlite3.connect(path) as connection:
        stored = connection.execute("SELECT value, write_id FROM records").fetchall()
    if persistent:
        assert receipt["status"] == "COMMITTED" and stored == [(str(imagePath.resolve()), receipt["writeId"])]
    else:
        assert receipt["status"] == "FAILED" and receipt["error"]["code"] == "E_SQLITE_VALUE"
        assert receipt["rowsAffected"] == 0 and stored == []


def testResolvedDirectoryAliasCannotHidePreviewCache(tmp_path):
    cache = tmp_path / "preview-cache"
    cache.mkdir()
    image = cache / "saved.png"
    image.write_bytes(cv2.imencode(".png", np.zeros((8, 8, 3), np.uint8))[1].tobytes())
    alias = tmp_path / "apparently-persistent"
    if os.name == "nt":
        # NTFS junctions do not require symbolic-link privilege. Arguments are
        # passed as individual values; cleanup remains pytest's owned tmp_path.
        result = subprocess.run(["cmd", "/d", "/c", "mklink", "/J", str(alias), str(cache)], capture_output=True)
        assert result.returncode == 0, result.stderr
    else:
        alias.symlink_to(cache, target_is_directory=True)
    assert (alias / image.name).is_file()
    with pytest.raises(SqliteWriterError, match="持久文件"):
        toStorage({"saved": True, "path": str(alias / image.name)}, "FILE_REFERENCE")
    with pytest.raises(SqliteWriterError, match="预览缓存"):
        resolveTarget(str(alias / "business.sqlite3"), tmp_path)


@pytest.mark.parametrize("first,second", [("Straße", "STRASSE"), ("K", "K"), ("İ", "i\u0307")])
def testDistinctUnicodeSQLiteColumnsCannotBorrowEachOthersTypeMetadata(tmp_path, first, second):
    path = tmp_path / "unicode.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute(f"CREATE TABLE records ({quoteIdentifier(first)} TEXT, {quoteIdentifier(second)} INTEGER)")
    wrong = {"column": first, "storageType": "INTEGER", "source": {"kind": "constant", "value": 7}}
    spec = config(path, [wrong])
    with pytest.raises(SqliteWriterError) as failed:
        validateMappings(spec, inspect(path, "records")["structure"])
    assert failed.value.code == "E_SQLITE_TYPE" and failed.value.row == 0
    with pytest.raises(WorkflowExecutionError) as failedRun:
        actualRun(path, None, rows=[wrong])
    assert failedRun.value.code == "E_SQLITE_TYPE"
    rows = [{"column": first, "storageType": "TEXT", "source": {"kind": "constant", "value": "actual"}}, wrong | {"column": second}]
    parseConfig(config(path, rows))
    receipt, _, _ = actualRun(path, None, rows=rows)
    assert receipt["status"] == "COMMITTED"
    with sqlite3.connect(path) as connection:
        assert connection.execute(f"SELECT {quoteIdentifier(first)}, typeof({quoteIdentifier(first)}), "
                                  f"{quoteIdentifier(second)}, typeof({quoteIdentifier(second)}) FROM records").fetchall() == [("actual", "text", 7, "integer")]


def testStructuredInitializationUsesSQLiteNameRulesAndStillRejectsAsciiDuplicates(tmp_path):
    path = tmp_path / "initialized.sqlite3"
    initialize(path, "records", [{"name": "Straße", "storageType": "TEXT"}, {"name": "STRASSE", "storageType": "INTEGER"}])
    rows = [{"column": "straße", "storageType": "TEXT", "source": {"kind": "constant", "value": "kept"}},
            {"column": "strasse", "storageType": "INTEGER", "source": {"kind": "constant", "value": 0}}]
    receipt, _, _ = actualRun(path, None, rows=rows)
    assert receipt["status"] == "COMMITTED"
    with sqlite3.connect(path) as connection:
        assert connection.execute('SELECT "Straße", "STRASSE" FROM records').fetchall() == [("kept", 0)]
    duplicates = [mapping(column="value"), mapping(column="VALUE")]
    with pytest.raises(SqliteWriterError, match="重复映射"):
        parseConfig(config(path, duplicates))
    with pytest.raises(SqliteWriterError, match="重复列"):
        initialize(tmp_path / "duplicate.sqlite3", "records", [{"name": "Value", "storageType": "TEXT"}, {"name": "VALUE", "storageType": "TEXT"}])


def testPersistentReferenceIsNormalizedAndTransientDatabaseTargetsAreRejected(tmp_path):
    image = tmp_path / "saved.png"
    image.write_bytes(b"explicit persistent file")
    spelling = str(tmp_path / "unused" / ".." / image.name)
    (tmp_path / "unused").mkdir()
    assert toStorage({"saved": True, "path": spelling}, "FILE_REFERENCE") == str(image.resolve())
    for directory in ("preview-cache", "preview_staging", "jobs"):
        with pytest.raises(SqliteWriterError):
            resolveTarget(str(tmp_path / directory / "business.sqlite3"), tmp_path)
