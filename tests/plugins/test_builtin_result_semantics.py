from pathlib import Path
import re

from emo_master import __version__
from emo_master.core.plugin.registry import PluginRegistry


def _assertChineseDescription(field, path):
    for key in ("title", "description"):
        assert re.search(r"[\u3400-\u9fff]", field.get(key, "")), f"{path}.{key}"


def _assertParameterFields(schema, path):
    properties = schema.get("properties", {})
    for name, definition in properties.items():
        leafPath = f"{path}.{name}"
        _assertChineseDescription(definition, leafPath)
        _assertParameterFields(definition, leafPath)
    for name, definition in schema.get("xFieldDescriptions", {}).items():
        _assertChineseDescription(definition, f"{path}.{name}")
    for key, suffix in (("items", "[]"), ("additionalProperties", ".*")):
        child = schema.get(key)
        if isinstance(child, dict):
            _assertChineseDescription(child, path + suffix)
            _assertParameterFields(child, path + suffix)


def _assertResultFields(fields, path):
    for key, field in fields.items():
        leafPath = f"{path}.{key}"
        _assertChineseDescription(field, leafPath)
        _assertResultFields(field.get("fields", {}), leafPath)
        if "items" in field:
            _assertResultFields({"[]": field["items"]}, leafPath)


def testBuiltinsRegisterWithChineseSemanticsForEveryActualPortAndParameter():
    root = Path(__file__).resolve().parents[2] / "src/emo_master/plugins/builtins"
    result = PluginRegistry(__version__).scan(root)
    assert not result.rejectedOperators
    assert len(result.activeOperators) == 82
    for operatorId, registration in result.activeOperators.items():
        manifest = registration.manifest
        semantics = manifest.resultSchema
        for section, ports in (("inputs", manifest.inputPorts), ("outputs", manifest.outputPorts)):
            assert set(semantics.get(section, {})) == set(ports), f"{operatorId}.{section}"
        for section in ("inputs", "outputs", "metrics", "diagnostics"):
            _assertResultFields(semantics.get(section, {}), f"{operatorId}.{section}")
        _assertParameterFields(manifest.paramSchema, operatorId)
