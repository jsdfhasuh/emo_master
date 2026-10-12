"""Diagnostic validity: real failed BEGIN, same DB, identified holder, no replay."""
import json
import sqlite3

import pytest

from emo_master.apps.runtime.context.sqlite_store import SqliteStore
from scripts.acceptance_sqlite_ledger import installed


def testLedgerDistinguishesFailedAdmissionFromHeldTransactionAndCommit(tmp_path):
    store = SqliteStore(tmp_path / "state.db")
    store.initialize()
    output = tmp_path / "ledger"
    output.mkdir()
    with installed(output, "known-job") as ledger:
        holder, waiter = store._connect(), store._connect()
        try:
            holder.execute("BEGIN IMMEDIATE")
            waiter.execute("PRAGMA busy_timeout=0")
            with pytest.raises(sqlite3.OperationalError):
                waiter.execute("BEGIN IMMEDIATE")
            assert holder.in_transaction and not waiter.in_transaction
            holder.commit()
            waiter.execute("BEGIN IMMEDIATE")
            waiter.commit()
        finally:
            holder.close()
            waiter.close()
    data = json.loads(next(output.glob("*.json")).read_text())
    assert not data["omitted"] and not data["observerErrors"]
    opens = [row for row in data["rows"] if row["phase"] == "connection_open"]
    assert len(opens) == 2 and len({row["connection"] for row in opens}) == 2
    assert {row["database"] for row in opens} == {str(store.dbPath.resolve())}
    begins = [row for row in data["rows"] if row["phase"] == "begin_immediate"]
    assert [row["outcome"] for row in begins] == ["returned", "raised", "returned"]
    assert [(row["before"], row["after"]) for row in begins] == [(False, True), (False, False), (False, True)]
    assert all(row["jobId"] == "known-job" for row in begins)
    commits = [row for row in data["rows"] if row["phase"] == "commit"]
    assert len(commits) == 2
    assert begins[1]["endNs"] < commits[0]["startNs"] < begins[2]["startNs"]
    assert commits[0]["successfulBegin"] == begins[0]["call"]
    assert all(row["before"] and not row["after"] for row in commits)
    assert len(ledger.rows) == len(data["rows"])
