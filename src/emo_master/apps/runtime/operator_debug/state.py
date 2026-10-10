"""Typed debug state with no production persistence or implicit live reads."""
from copy import deepcopy
import time

from emo_master.apps.runtime.context.global_variables import VariableRecord
from emo_master.apps.runtime.context.global_counters import GlobalCounterRecord, validateGlobalCounterName, validateGlobalCounterValue
from emo_master.core.project.global_variables import VariableError, definitions, validateValue
from .contracts import encode


class DebugVariables:
    def __init__(self, declarations, values=None):
        self.definitions = definitions(declarations)
        self.values = {key: deepcopy(row.initialValue) for key, row in self.definitions.items()}
        self.revisions = {key: 1 for key in self.definitions}
        for key, value in (values or {}).items():
            definition = self._definition(key)
            validateValue(value, definition.type, legacy=definition.legacyCounterName is not None)
            if definition.kind == "constant" and value != definition.initialValue:
                raise VariableError("E_VARIABLE_READ_ONLY", "constant snapshot differs from its definition")
            self.values[key] = deepcopy(value)
        encode(self.values, 16 * 1024)

    def _definition(self, key):
        if key not in self.definitions:
            raise VariableError("E_VARIABLE_UNKNOWN", "unknown variable: " + str(key))
        return self.definitions[key]

    def readMany(self, keys):
        return {key: self.get(key) for key in keys}

    def get(self, key):
        self._definition(key)
        return deepcopy(self.values[key])

    def records(self, keys=None):
        return {key: VariableRecord(key, self.get(key), self.revisions[key], 0)
                for key in (self.definitions if keys is None else keys)}

    def set(self, key, value, expectedRevision=None):
        definition = self._definition(key)
        if definition.kind == "constant":
            raise VariableError("E_VARIABLE_READ_ONLY", "constants cannot be written")
        if expectedRevision is not None and expectedRevision != self.revisions[key]:
            raise VariableError("E_VARIABLE_CONFLICT", "variable revision changed")
        validateValue(value, definition.type, legacy=definition.legacyCounterName is not None)
        encode(dict(self.values, **{key: value}), 16 * 1024)
        self.values[key] = deepcopy(value)
        self.revisions[key] += 1
        return self.get(key)

    def reset(self, key, expectedRevision=None):
        return self.set(key, self._definition(key).initialValue, expectedRevision)

    def increment(self, key, delta=1):
        if self._definition(key).type != "integer" or type(delta) is not int:
            raise VariableError("E_VARIABLE_TYPE", "increment requires an integer variable and delta")
        return self.set(key, self.get(key) + delta)


class DebugCounters:
    def __init__(self, variables):
        self.variables = variables
        self.values = {}

    def apply(self, name, *, increment=False, reset=False):
        validateGlobalCounterName(name)
        key = "counter:" + name
        for other, definition in self.variables.definitions.items():
            if definition.name == name and (other != key or definition.legacyCounterName != name):
                raise VariableError("E_VARIABLE_NAME_CONFLICT", "counter conflicts with a declared variable")
        declared = key in self.variables.definitions
        if not declared and name not in self.values and len(self.values) >= 128:
            raise VariableError("E_DEBUG_LIMIT", "debug counter quota exceeded")
        value = self.variables.get(key) if declared else self.values.get(name, 0)
        value = 0 if reset else value + int(increment)
        validateGlobalCounterValue(value)
        if declared:
            self.variables.set(key, value)
        else:
            self.values[name] = value
        return GlobalCounterRecord(name, value, int(time.time()*1000))
