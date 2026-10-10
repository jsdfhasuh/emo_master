"""Project/job scoped typed state backed by the Runtime SQLite database."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import sqlite3
import time
import random

from emo_master.core.project.global_variables import VariableDefinition, VariableError, definitions, validateValue


@dataclass(frozen=True)
class VariableRecord:
    variableId: str
    value: object
    revision: int
    updatedAtMs: int

    def toDict(self):
        return asdict(self)


def migrateVariables(connection):
    connection.execute("""CREATE TABLE IF NOT EXISTS variableDefinitions (
        projectId TEXT NOT NULL, variableId TEXT NOT NULL, definitionJson TEXT NOT NULL,
        legacyName TEXT, PRIMARY KEY(projectId, variableId), UNIQUE(projectId, legacyName))""")
    connection.execute("""CREATE TABLE IF NOT EXISTS variableValues (
        projectId TEXT NOT NULL, variableId TEXT NOT NULL, scope TEXT NOT NULL,
        valueJson TEXT NOT NULL, revision INTEGER NOT NULL, updatedAtMs INTEGER NOT NULL,
        PRIMARY KEY(projectId, variableId, scope))""")
    old = connection.execute("SELECT type FROM sqlite_master WHERE name='globalCounters'").fetchone()
    if old and old[0] == "table":
        for projectId, name, value, updated in connection.execute(
            "SELECT projectId, name, value, updatedAtMs FROM globalCounters"
        ).fetchall():
            variableId = "counter:" + name
            definition = counterDefinition(name)
            connection.execute("INSERT OR IGNORE INTO variableDefinitions VALUES (?, ?, ?, ?)",
                               (projectId, variableId, definition.model_dump_json(), name))
            connection.execute("INSERT OR IGNORE INTO variableValues VALUES (?, ?, '', ?, 1, ?)",
                               (projectId, variableId, json.dumps(value), updated))
        # Keep the original rows as a recovery archive, never as a second writer.
        connection.execute("ALTER TABLE globalCounters RENAME TO globalCountersLegacy")
        connection.execute("""CREATE VIEW globalCounters AS
            SELECT d.projectId, d.legacyName AS name, CAST(v.valueJson AS INTEGER) AS value, v.updatedAtMs
            FROM variableDefinitions d JOIN variableValues v
            ON d.projectId=v.projectId AND d.variableId=v.variableId
            WHERE d.legacyName IS NOT NULL AND v.scope=''""")


def _beginWrite(connection):
    """Bounded, jittered admission avoids starvation behind event commits.

    SQLite's increasing busy sleeps can repeatedly miss short gaps between
    durable event writes. Retry ONLY BEGIN, before reading/changing a value.
    The transaction body and commit are executed once, never replayed.
    """
    timeout = connection.execute("PRAGMA busy_timeout").fetchone()[0]
    deadline = time.monotonic() + timeout / 1000
    connection.execute("PRAGMA busy_timeout=0")
    try:
        while True:
            try:
                connection.execute("BEGIN IMMEDIATE")
                return
            except sqlite3.OperationalError as error:
                if not any(word in str(error).lower() for word in ("locked", "busy")):
                    raise
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise VariableError("E_VARIABLE_BUSY", "global variable database is busy") from error
                time.sleep(min(remaining, random.uniform(.001, .005)))
    finally:
        connection.execute(f"PRAGMA busy_timeout={int(timeout)}")


def counterDefinition(name):
    return VariableDefinition(name=name, type="integer", kind="variable", initialValue=0,
                              lifetime="persistent", legacyCounterName=name)


def legacyDefinitions(store, projectId):
    with store._connect() as connection:
        return {key: json.loads(raw) for key, raw in connection.execute(
            "SELECT variableId, definitionJson FROM variableDefinitions WHERE projectId=? AND legacyName IS NOT NULL",
            (projectId,))}


class ProjectGlobalVariables:
    def __init__(self, store, projectId, variableDefinitions, jobId=""):
        self.store, self.projectId, self.jobId = store, projectId, jobId
        self.definitions = definitions(variableDefinitions)

    def synchronize(self):
        with self.store._connect() as connection:
            _beginWrite(connection)
            legacy = {key: json.loads(raw)["name"] for key, raw in connection.execute(
                "SELECT variableId, definitionJson FROM variableDefinitions WHERE projectId=? AND legacyName IS NOT NULL",
                (self.projectId,))}
            for key, definition in self.definitions.items():
                if any(name == definition.name and other != key for other, name in legacy.items()):
                    raise VariableError("E_VARIABLE_NAME_CONFLICT", "variable name conflicts with a legacy counter")
            for key, definition in self.definitions.items():
                old = connection.execute(
                    "SELECT definitionJson FROM variableDefinitions WHERE projectId=? AND variableId=?",
                    (self.projectId, key)).fetchone()
                if old:
                    previous = VariableDefinition.model_validate_json(old[0])
                    if any(getattr(previous, field) != getattr(definition, field)
                           for field in ("type", "kind", "lifetime", "legacyCounterName")):
                        raise VariableError("E_VARIABLE_DEFINITION_CHANGED", "variable identity/type/lifetime changed")
                connection.execute("""INSERT INTO variableDefinitions VALUES (?, ?, ?, ?)
                    ON CONFLICT(projectId,variableId) DO UPDATE SET definitionJson=excluded.definitionJson""",
                    (self.projectId, key, definition.model_dump_json(), definition.legacyCounterName))
                if definition.kind == "variable" and definition.lifetime == "persistent":
                    self._initialize(connection, key, definition, "")

    def initializeJob(self):
        if not self.jobId:
            raise VariableError("E_VARIABLE_JOB_REQUIRED", "job ID is required")
        with self.store._connect() as connection:
            _beginWrite(connection)
            for key, definition in self.definitions.items():
                if definition.kind == "variable" and definition.lifetime == "job":
                    self._initialize(connection, key, definition, self.jobId)

    def _initialize(self, connection, key, definition, scope):
        connection.execute("INSERT OR IGNORE INTO variableValues VALUES (?, ?, ?, ?, 1, ?)",
                           (self.projectId, key, scope, json.dumps(definition.initialValue, allow_nan=False), _now()))

    def _definition(self, key):
        definition = self.definitions.get(key)
        if definition is None:
            raise VariableError("E_VARIABLE_UNKNOWN", f"unknown variable: {key}")
        return definition

    def _scope(self, definition):
        if definition.lifetime == "persistent" or definition.kind == "constant":
            return ""
        if not self.jobId:
            raise VariableError("E_VARIABLE_JOB_REQUIRED", "select a job for this variable")
        return self.jobId

    def _read(self, connection, key):
        definition = self._definition(key)
        if definition.kind == "constant":
            return VariableRecord(key, definition.initialValue, 0, 0)
        row = connection.execute("""SELECT valueJson, revision, updatedAtMs FROM variableValues
            WHERE projectId=? AND variableId=? AND scope=?""",
            (self.projectId, key, self._scope(definition))).fetchone()
        if row is None:
            raise VariableError("E_VARIABLE_NOT_INITIALIZED", "variable is not initialized for this project/job")
        value = json.loads(row[0])
        validateValue(value, definition.type, legacy=definition.legacyCounterName is not None)
        return VariableRecord(key, value, row[1], row[2])

    def records(self, keys=None):
        with self.store._connect() as connection:
            connection.execute("BEGIN")
            return {key: self._read(connection, key) for key in (self.definitions if keys is None else keys)}

    def readMany(self, keys):
        return {key: record.value for key, record in self.records(dict.fromkeys(keys)).items()}

    def get(self, key):
        return self.readMany([key])[key]

    def _mutate(self, key, transform, expectedRevision=None):
        definition = self._definition(key)
        if definition.kind == "constant":
            raise VariableError("E_VARIABLE_READ_ONLY", "constants cannot be written")
        with self.store._connect() as connection:
            _beginWrite(connection)
            old = self._read(connection, key)
            if expectedRevision is not None and expectedRevision != old.revision:
                raise VariableError("E_VARIABLE_CONFLICT", "value changed; refresh before setting it again")
            value = transform(old.value)
            validateValue(value, definition.type, legacy=definition.legacyCounterName is not None)
            updated = _now()
            connection.execute("""UPDATE variableValues SET valueJson=?, revision=revision+1, updatedAtMs=?
                WHERE projectId=? AND variableId=? AND scope=?""",
                (json.dumps(value, allow_nan=False), updated, self.projectId, key, self._scope(definition)))
            return VariableRecord(key, value, old.revision + 1, updated)

    def set(self, key, value, expectedRevision=None):
        return self._mutate(key, lambda _: value, expectedRevision).value

    def reset(self, key, expectedRevision=None):
        return self.set(key, self._definition(key).initialValue, expectedRevision)

    def increment(self, key, delta=1):
        if self._definition(key).type != "integer" or type(delta) is not int:
            raise VariableError("E_VARIABLE_TYPE", "increment requires an integer variable and delta")
        return self._mutate(key, lambda value: value + delta).value

    def snapshot(self):
        return ReadOnlyVariables(self.readMany(self.definitions))


class ReadOnlyVariables:
    def __init__(self, values):
        self.values = dict(values)

    def get(self, key):
        if key not in self.values:
            raise VariableError("E_VARIABLE_UNKNOWN", f"unknown variable: {key}")
        return self.values[key]

    def readMany(self, keys):
        return {key: self.get(key) for key in keys}

    def set(self, *args, **kwargs):
        raise VariableError("E_VARIABLE_PREVIEW_READ_ONLY", "preview cannot change global variables")

    reset = increment = set


def counterOperation(store, projectId, name, *, increment=False, reset=False, value=None):
    from emo_master.apps.runtime.context.global_counters import (
        GlobalCounterError, GlobalCounterRecord, validateGlobalCounterName, validateGlobalCounterValue,
    )
    validateGlobalCounterName(name)
    if value is not None:
        validateGlobalCounterValue(value)
    key = "counter:" + name
    with store._connect() as connection:
        existing = connection.execute("SELECT variableId, definitionJson FROM variableDefinitions WHERE projectId=?", (projectId,)).fetchall()
        if any(other != key and json.loads(raw)["name"] == name for other, raw in existing):
            raise GlobalCounterError("E_VARIABLE_NAME_CONFLICT", "counter name conflicts with an existing variable")
    accessor = ProjectGlobalVariables(store, projectId, {key: counterDefinition(name)})
    try:
        accessor.synchronize()
        if value is not None or reset or increment:
            record = accessor._mutate(key, lambda current: value if value is not None else 0 if reset else current + 1)
        else:
            record = accessor.records([key])[key]
        return GlobalCounterRecord(name, record.value, record.updatedAtMs)
    except VariableError as error:
        code = {"E_VARIABLE_TYPE": "E_COUNTER_VALUE_RANGE", "E_VARIABLE_BUSY": "E_COUNTER_BUSY"}.get(error.code, error.code)
        raise GlobalCounterError(code, str(error)) from error
    except sqlite3.OperationalError as error:
        raise GlobalCounterError("E_COUNTER_BUSY" if "locked" in str(error) or "busy" in str(error)
                                 else "E_RUNTIME_STATE_UNAVAILABLE", str(error)) from error


def _now():
    return int(time.time() * 1000)
