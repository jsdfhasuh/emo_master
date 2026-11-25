"""Check the current registered built-ins against the PR20 field audit."""

from collections import Counter
import json
from pathlib import Path
import re

from emo_master import __version__
from emo_master.core.plugin.registry import PluginRegistry
from emo_master.plugins import builtins


def _pathParts(path):
    return path.replace("[]", ".[]").split(".")


def _parameterAt(schema, path):
    for part in _pathParts(path):
        if part == "[]":
            schema = schema["items"]
        elif part == "*":
            schema = schema["additionalProperties"]
        else:
            schema = schema["properties"].get(part) or schema.get("xFieldDescriptions", {})[part]
    return schema


def _resultAt(fields, path):
    key, *parts = _pathParts(path)
    value = fields[key]
    for part in parts:
        if part == "[]":
            value = value["items"]
        elif part != "*":
            value = value["fields"][part]
    return value


def _checkField(field, operatorId, path):
    for name in ("title", "description"):
        assert re.search(r"[\u3400-\u9fff]", field.get(name, "")), f"{operatorId}.{path}.{name}"


def main():
    audit = json.loads(Path(__file__).with_name("operator-field-audit.json").read_text(encoding="utf-8"))
    scan = PluginRegistry(__version__).scan(Path(builtins.__file__).parent)
    assert not scan.rejectedOperators, scan.rejectedOperators
    assert set(scan.activeOperators) == {row["operatorId"] for row in audit["operators"]}
    coverage = Counter()
    for row in audit["operators"]:
        operatorId = row["operatorId"]
        manifest = scan.activeOperators[operatorId].manifest
        assert (manifest.version, manifest.entry) == (row["version"], row["entry"]), operatorId
        coverage[row["reviewGroup"]] += 1
        result = manifest.resultSchema
        for section, ports in (("inputs", manifest.inputPorts), ("outputs", manifest.outputPorts)):
            assert set(ports) == set(result.get(section, {})), (operatorId, section)
            for port in row[section]:
                key = port["key"]
                assert ports[key] == port["contract"], (operatorId, section, key)
                _checkField(result[section][key], operatorId, f"{section}.{key}")
                coverage[section] += 1
        for parameter in row["parameters"]:
            path = parameter["path"]
            _checkField(_parameterAt(manifest.paramSchema, path), operatorId, f"params.{path}")
            coverage["parameters"] += 1
        for reviewSection, resultSection in (
            ("metrics", "metrics"), ("diagnostics", "diagnostics"), ("outputFields", "outputs"),
        ):
            for reviewedField in row[reviewSection]:
                path = reviewedField["path"]
                if (operatorId, resultSection, path) == ("vision.demo.empty", "metrics", "latencyMs"):
                    assert path not in result.get("metrics", {})
                    coverage["removedMetricPlaceholder"] += 1
                    continue
                _checkField(_resultAt(result[resultSection], path), operatorId, f"{resultSection}.{path}")
                coverage[reviewSection] += 1
    assert coverage["removedMetricPlaceholder"] == 1
    print(f"{len(scan.activeOperators)} registered built-in operators, no rejections")
    for name, count in sorted(coverage.items()):
        print(f"{name}: {count}")


if __name__ == "__main__":
    main()
