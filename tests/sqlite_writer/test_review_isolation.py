"""Database-level debug ownership, including real cross-table Runner writes."""
from copy import deepcopy
import os
from pathlib import Path
import sqlite3

import pytest

from emo_master.apps.runtime.business_sqlite.backend import freezeTargets
from emo_master.apps.runtime.workflow.cancellation import CancellationToken
from emo_master.apps.runtime.workflow.context import RunContext
from emo_master.apps.runtime.workflow.runner import WorkflowRunner
from emo_master.core.contracts.sqlite_writer import OPERATOR_ID, SqliteWriterError
from emo_master.core.project.models import ProjectDocument
from emo_master.core.workflow.compiler import WorkflowCompiler
from emo_master.plugins.builtins.sqlite_writer.operator import SqliteWriterOperator
from tests.sqlite_writer.test_dependencies import config, project


def constant(column, value, storage="INTEGER"):
    return {"column": column, "storageType": storage, "source": {"kind": "constant", "value": value}}


def twoTables(path, *, foreignKey=True):
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE parents (id INTEGER PRIMARY KEY, write_id TEXT NOT NULL UNIQUE, value INTEGER)")
        connection.execute("CREATE TABLE children (id INTEGER PRIMARY KEY, write_id TEXT NOT NULL UNIQUE, "
                           "parent_receipt TEXT NOT NULL, parent_id INTEGER NOT NULL" +
                           (" REFERENCES parents(id)" if foreignKey else "") + ")")


def document(path, template=None, childPath=None, *, reverse=False):
    parent = config(path, rows=[constant("value", 7)])
    parent["table"] = "parents"
    child = config(childPath or path, rows=[constant("parent_id", 1), {
        "column": "parent_receipt", "storageType": "JSON",
        "source": {"kind": "node_output", "nodeId": "parent-writer", "port": "receipt"}}])
    child["table"] = "children"
    if template is not None:
        parent["debugDatabasePath"] = child["debugDatabasePath"] = str(template)
    nodes = [{"nodeId": "parent-writer", "operatorId": OPERATOR_ID, "params": parent},
             {"nodeId": "child-writer", "operatorId": OPERATOR_ID, "params": child}]
    return ProjectDocument.model_validate(project(nodes=list(reversed(nodes)) if reverse else nodes))


def execute(doc, job):
    operators = {OPERATOR_ID: SqliteWriterOperator}
    compiled = WorkflowCompiler(operators).compile(doc)
    events = []
    WorkflowRunner(compiled, operators, eventPublisher=lambda **row: events.append(row)).run(
        "main", {"value": 7}, RunContext.root(job, "main"), CancellationToken())
    receipts = [row["payload"]["receipt"] for row in events if row["eventType"] == "sqlite.write.finished"]
    assert len(receipts) == 2 and all(row["status"] == "COMMITTED" for row in receipts)
    assert receipts[0]["execution"]["workflowRunId"] == receipts[1]["execution"]["workflowRunId"]
    assert receipts[0]["execution"]["nodeId"] == "parent-writer"
    return receipts


@pytest.mark.parametrize("alias", ["relative", "case", "hardlink"])
@pytest.mark.parametrize("reverse", [False, True])
def testSameDatabaseDebugTablesShareWritesAndForeignKeysWithoutChangingSources(tmp_path, alias, reverse):
    business = tmp_path / "业务.sqlite3"
    template = tmp_path / "模板 空格.sqlite3"
    twoTables(business)
    twoTables(template)
    if alias == "relative":
        child = business.name
    elif alias == "case":
        if os.name != "nt":
            pytest.skip("Windows case-insensitive filesystem regression")
        child = str(business.with_suffix(".SQLITE3"))
    else:
        child = tmp_path / "business-alias.sqlite3"
        os.link(business, child)
    doc = document(business, template, child, reverse=reverse)
    originalDraft = doc.model_dump()
    execute(freezeTargets(doc, tmp_path), "release")
    businessBytes, templateBytes = business.read_bytes(), template.read_bytes()
    prepared = freezeTargets(doc, tmp_path, debugRoot=tmp_path / "debug")
    paths = {node.params["databasePath"] for node in prepared.workflows["main"].nodes if node.operatorId == OPERATOR_ID}
    assert len(paths) == 1
    isolated = Path(next(iter(paths)))
    assert isolated.is_relative_to(tmp_path / "debug")
    execute(prepared, "debug")
    with sqlite3.connect(isolated) as connection:
        assert connection.execute("SELECT value FROM parents").fetchall() == [(7,)]
        assert connection.execute("SELECT parent_id FROM children").fetchall() == [(1,)]
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert business.read_bytes() == businessBytes and template.read_bytes() == templateBytes
    assert doc.model_dump() == originalDraft
    # Preparing the same owned namespace again must not overwrite its writes.
    again = freezeTargets(doc, tmp_path, debugRoot=tmp_path / "debug")
    assert {node.params["databasePath"] for node in again.workflows["main"].nodes if node.operatorId == OPERATOR_ID} == paths
    with sqlite3.connect(isolated) as connection:
        assert connection.execute("SELECT COUNT(*) FROM parents").fetchone()[0] == 1


def testSupportedSchemasShareOneDebugDatabaseWithoutCopyingBusinessRows(tmp_path):
    business = tmp_path / "business.sqlite3"
    twoTables(business, foreignKey=False)
    doc = document(business)
    execute(freezeTargets(doc, tmp_path), "release")
    original = business.read_bytes()
    prepared = freezeTargets(doc, tmp_path, debugRoot=tmp_path / "debug")
    execute(prepared, "debug")
    paths = {node.params["databasePath"] for node in prepared.workflows["main"].nodes if node.operatorId == OPERATOR_ID}
    assert len(paths) == 1 and business.read_bytes() == original
    with sqlite3.connect(next(iter(paths))) as connection:
        assert connection.execute("SELECT COUNT(*) FROM parents").fetchone()[0] == 1


def testConflictingTemplatesRejectBeforeCreatingDebugDatabase(tmp_path):
    business, first, second = (tmp_path / name for name in ("business.sqlite3", "first.sqlite3", "second.sqlite3"))
    for path in (business, first, second):
        twoTables(path)
    doc = document(business, first)
    doc.workflows["main"].nodes[1].params["debugDatabasePath"] = str(second)
    before = {path: path.read_bytes() for path in (business, first, second)}
    with pytest.raises(SqliteWriterError, match="同一个专用测试库"):
        freezeTargets(doc, tmp_path, debugRoot=tmp_path / "debug")
    assert not (tmp_path / "debug").exists()
    assert all(path.read_bytes() == data for path, data in before.items())


def testOneTemplateAppliesToAllMappedTablesAndMissingTableCannotBeInvented(tmp_path):
    business, template = (tmp_path / name for name in ("business.sqlite3", "template.sqlite3"))
    twoTables(business)
    twoTables(template)
    doc = document(business, template)
    del doc.workflows["main"].nodes[1].params["debugDatabasePath"]
    prepared = freezeTargets(doc, tmp_path, debugRoot=tmp_path / "debug")
    execute(prepared, "debug")
    with sqlite3.connect(template) as connection:
        connection.execute("DROP TABLE children")
    with pytest.raises(SqliteWriterError, match="缺少目标表"):
        freezeTargets(doc, tmp_path, debugRoot=tmp_path / "missing-table")
    assert not (tmp_path / "missing-table").exists()


def testAnotherWritersBusinessDatabaseCannotBecomeDebugTemplate(tmp_path):
    business, other = (tmp_path / name for name in ("business.sqlite3", "other-business.sqlite3"))
    twoTables(business)
    twoTables(other)
    doc = document(business, other, other)
    before = deepcopy(doc.model_dump())
    with pytest.raises(SqliteWriterError, match="内部库|文件别名"):
        freezeTargets(doc, tmp_path, debugRoot=tmp_path / "debug")
    assert doc.model_dump() == before and not (tmp_path / "debug").exists()
