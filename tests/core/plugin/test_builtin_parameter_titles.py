from pathlib import Path
import re

from emo_master.core.plugin.registry import PluginRegistry


def _fields(schema):
    for name, field in schema.get("properties", {}).items():
        yield name, field
        yield from _fields(field)
    items = schema.get("items")
    if isinstance(items, dict):
        yield from _fields(items)


def testAllBuiltinParameterTitlesSurviveRegistration():
    root = Path(__file__).resolve().parents[3] / "src/emo_master/plugins/builtins"
    scan = PluginRegistry("0.6.1").scan(root)
    assert not scan.rejectedOperators
    assert len(scan.activeOperators) == len(list(root.rglob("manifest.json"))) == 53
    fieldCount = 0
    for operatorId, descriptor in scan.activeOperators.items():
        for name, field in _fields(descriptor.manifest.paramSchema):
            title = field.get("title")
            assert isinstance(title, str) and title.strip(), (operatorId, name)
            assert re.search(r"[\u4e00-\u9fff]", title), (operatorId, name, title)
            fieldCount += 1
    assert fieldCount >= 295
