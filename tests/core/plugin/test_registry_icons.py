from pathlib import Path

from emo_master.core.plugin.registry import PluginRegistry
from tests.core.plugin.test_registry import (
    _validManifest,
    _validOperatorSource,
    _writeManifest,
    _writeOperatorPackage,
)
from tests.icon_fixtures import SVG


def testRegistryIconIssuesAreIndependentAndResourcesFrozen(tmp_path: Path, monkeypatch):
    entry = _writeOperatorPackage(tmp_path, "icon_plugin", _validOperatorSource())
    monkeypatch.syspath_prepend(str(tmp_path))
    root = tmp_path / "plugins"
    payload = _validManifest(entry=entry)
    payload["iconResource"] = "icon.svg"
    manifest = _writeManifest(root, "icon", payload)
    registry = PluginRegistry("0.1.0")
    missing = registry.scan(root)
    assert not missing.rejectedOperators
    assert missing.activeOperators["vision.demo.temp"].iconStatus == "invalid"
    (manifest.parent / "icon.svg").write_bytes(SVG)
    descriptor = registry.scan(root).activeOperators["vision.demo.temp"]
    assert descriptor.iconStatus == "ready"
    assert descriptor.iconAsset.content == SVG
    (manifest.parent / "icon.svg").unlink()
    assert descriptor.iconAsset.content == SVG
    assert (
        registry.scan(root).activeOperators["vision.demo.temp"].iconStatus == "invalid"
    )


def testBadDeclarationDoesNotRejectValidPluginOrRereadManifest(
    tmp_path: Path, monkeypatch
):
    entry = _writeOperatorPackage(tmp_path, "bad_icon_plugin", _validOperatorSource())
    monkeypatch.syspath_prepend(str(tmp_path))
    root = tmp_path / "plugins"
    payload = _validManifest(entry=entry)
    payload["iconResource"] = None
    _writeManifest(root, "icon", payload)
    registry = PluginRegistry("0.1.0")
    reads = []
    original = registry._readManifest

    def read(path):
        reads.append(path)
        return original(path)

    monkeypatch.setattr(registry, "_readManifest", read)
    result = registry.scan(root)
    assert len(reads) == 1
    assert not result.rejectedOperators
    assert (
        result.activeOperators["vision.demo.temp"].iconIssues[0].code
        == "E_ICON_SPEC_INVALID"
    )
