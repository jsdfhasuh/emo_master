import json
from pathlib import Path

from emo_master.core.plugin.registry import PluginRegistry
from emo_master.core.plugin.validator import isVersionCompatible


def testRegistryLoadsValidManifest(tmp_path: Path, monkeypatch) -> None:
    entry = _writeOperatorPackage(tmp_path, "valid_temp_plugin", _validOperatorSource())
    pluginRoot = tmp_path / "plugins"
    _writeManifest(pluginRoot, "temp_edge", _validManifest(entry=entry))

    monkeypatch.syspath_prepend(str(tmp_path))
    scanResult = PluginRegistry(coreVersion="0.1.0").scan(pluginRoot)

    assert "vision.demo.temp" in scanResult.activeOperators
    assert scanResult.rejectedOperators == {}


def testRegistryLoadsValidatedEditorResourceWithoutImportingController(
    tmp_path: Path, monkeypatch
) -> None:
    packageName = "editor_temp_plugin"
    entry = _writeOperatorPackage(tmp_path, packageName, _validOperatorSource())
    pluginRoot = tmp_path / "plugins"
    payload = _validManifest(entry=entry)
    payload["editor"] = {
        "schemaVersion": "1.0",
        "kind": "customUi",
        "openMode": "window",
        "uiResource": "ui/editor.ui",
        "controllerEntry": f"{packageName}.editor:TempEditorController",
        "fallback": "schemaForm",
        "previewMode": "pure",
    }
    manifestPath = _writeManifest(pluginRoot, "temp_editor", payload)
    uiPath = manifestPath.parent / "ui" / "editor.ui"
    uiPath.parent.mkdir()
    uiPath.write_text(
        "<?xml version='1.0' encoding='UTF-8'?><ui version='4.0'><class>Editor</class>"
        "<widget class='QWidget' name='Editor'/><resources/><connections/></ui>",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))

    scanResult = PluginRegistry(coreVersion="0.1.0").scan(pluginRoot)

    descriptor = scanResult.activeOperators["vision.demo.temp"]
    assert descriptor.manifest.editor is not None
    assert descriptor.manifest.editor.previewMode == "pure"
    assert descriptor.resourceRoot == manifestPath.parent
    assert descriptor.editorIssues == ()
    assert scanResult.rejectedOperators == {}


def testInvalidEditorFallsBackWithoutRejectingRuntimeOperator(
    tmp_path: Path, monkeypatch
) -> None:
    packageName = "unsafe_editor_plugin"
    entry = _writeOperatorPackage(tmp_path, packageName, _validOperatorSource())
    pluginRoot = tmp_path / "plugins"
    payload = _validManifest(entry=entry)
    payload["editor"] = {
        "uiResource": "../outside.ui",
        "controllerEntry": f"{packageName}.editor:TempEditorController",
    }
    _writeManifest(pluginRoot, "unsafe_editor", payload)
    monkeypatch.syspath_prepend(str(tmp_path))

    scanResult = PluginRegistry(coreVersion="0.1.0").scan(pluginRoot)

    descriptor = scanResult.activeOperators["vision.demo.temp"]
    assert descriptor.manifest.editor is None
    assert {issue.code for issue in descriptor.editorIssues} == {
        "E_EDITOR_ASSET_INVALID"
    }
    assert scanResult.rejectedOperators == {}


def testRegistryRejectsInvalidEntry(tmp_path: Path) -> None:
    pluginRoot = tmp_path / "plugins"
    _writeManifest(
        pluginRoot,
        "bad_edge",
        _validManifest(
            operatorId="vision.demo.bad",
            displayName="Bad",
            entry="bad.module:MissingClass",
        ),
    )

    scanResult = PluginRegistry(coreVersion="0.1.0").scan(pluginRoot)

    assert "vision.demo.bad" in scanResult.rejectedOperators
    assert "vision.demo.bad" not in scanResult.activeOperators
    assert _codes(scanResult.rejectedOperators["vision.demo.bad"]) == {
        "E_ENTRY_IMPORT_FAILED"
    }


def testRegistryRejectsDuplicateIdsAcrossRoots(tmp_path: Path) -> None:
    firstRoot = tmp_path / "first"
    secondRoot = tmp_path / "second"
    payload = _validManifest(entry="unused.module:UnusedOperator")
    firstManifest = _writeManifest(firstRoot, "first_copy", payload)
    secondManifest = _writeManifest(secondRoot, "second_copy", payload)

    scanResult = PluginRegistry(coreVersion="0.1.0").scanRoots((firstRoot, secondRoot))

    assert "vision.demo.temp" not in scanResult.activeOperators
    issues = scanResult.rejectedOperators["vision.demo.temp"]
    assert _codes(issues) == {"E_OPERATOR_ID_DUPLICATED"}
    assert str(firstManifest) in issues[0].message
    assert str(secondManifest) in issues[0].message


def testRegistryRejectsDuplicateIdsWithinOneRoot(tmp_path: Path) -> None:
    pluginRoot = tmp_path / "plugins"
    payload = _validManifest(entry="unused.module:UnusedOperator")
    _writeManifest(pluginRoot, "first_copy", payload)
    _writeManifest(pluginRoot, "second_copy", payload)

    scanResult = PluginRegistry(coreVersion="0.1.0").scan(pluginRoot)

    assert "vision.demo.temp" not in scanResult.activeOperators
    assert _codes(scanResult.rejectedOperators["vision.demo.temp"]) == {
        "E_OPERATOR_ID_DUPLICATED"
    }


def testRegistryDeduplicatesRepeatedRoots(tmp_path: Path, monkeypatch) -> None:
    entry = _writeOperatorPackage(
        tmp_path, "repeat_temp_plugin", _validOperatorSource()
    )
    pluginRoot = tmp_path / "plugins"
    _writeManifest(pluginRoot, "temp_edge", _validManifest(entry=entry))
    monkeypatch.syspath_prepend(str(tmp_path))

    scanResult = PluginRegistry(coreVersion="0.1.0").scanRoots(
        (pluginRoot, pluginRoot.resolve())
    )

    assert set(scanResult.activeOperators) == {"vision.demo.temp"}
    assert scanResult.rejectedOperators == {}


def testRegistryReportsMissingPluginRoot(tmp_path: Path) -> None:
    missingRoot = tmp_path / "missing"

    scanResult = PluginRegistry(coreVersion="0.1.0").scan(missingRoot)

    issues = scanResult.rejectedOperators[str(missingRoot)]
    assert _codes(issues) == {"E_PLUGIN_ROOT_NOT_FOUND"}


def testRegistryDoesNotCoerceManifestScalarTypes(tmp_path: Path) -> None:
    pluginRoot = tmp_path / "plugins"
    payload = _validManifest(entry="unused.module:UnusedOperator")
    payload["displayName"] = 123
    _writeManifest(pluginRoot, "bad_scalar", payload)

    scanResult = PluginRegistry(coreVersion="0.1.0").scan(pluginRoot)

    issues = scanResult.rejectedOperators["vision.demo.temp"]
    assert "E_MANIFEST_TYPE_INVALID" in _codes(issues)
    assert "vision.demo.temp" not in scanResult.activeOperators


def testRegistryRejectsInvalidVersionWithoutCrashing(tmp_path: Path) -> None:
    pluginRoot = tmp_path / "plugins"
    payload = _validManifest(entry="unused.module:UnusedOperator")
    payload["minCoreVersion"] = "not-a-version"
    _writeManifest(pluginRoot, "bad_version", payload)

    scanResult = PluginRegistry(coreVersion="0.1.0").scan(pluginRoot)

    issues = scanResult.rejectedOperators["vision.demo.temp"]
    assert "E_MANIFEST_VERSION_INVALID" in _codes(issues)


def testRegistryRequiresCallableOperatorMethods(tmp_path: Path, monkeypatch) -> None:
    entry = _writeOperatorPackage(
        tmp_path,
        "non_callable_plugin",
        "class TempOperator:\n"
        "    class Meta:\n"
        "        inputPorts = {}\n"
        "        outputPorts = {}\n"
        "    meta = Meta()\n"
        "    validateParams = None\n"
        "    def executeNode(self, inputs, params, runtimeContext):\n"
        "        return {'status': 'ok', 'outputs': {}}\n",
    )
    pluginRoot = tmp_path / "plugins"
    _writeManifest(pluginRoot, "non_callable", _validManifest(entry=entry))
    monkeypatch.syspath_prepend(str(tmp_path))

    scanResult = PluginRegistry(coreVersion="0.1.0").scan(pluginRoot)

    issues = scanResult.rejectedOperators["vision.demo.temp"]
    assert _codes(issues) == {"E_OPERATOR_METHOD_NOT_CALLABLE"}


def testRegistryContainsUnexpectedMetaValidationErrors(
    tmp_path: Path,
    monkeypatch,
) -> None:
    entry = _writeOperatorPackage(
        tmp_path,
        "raising_meta_plugin",
        "class TempOperator:\n"
        "    class Meta:\n"
        "        @property\n"
        "        def inputPorts(self):\n"
        "            raise RuntimeError('broken metadata')\n"
        "        outputPorts = {}\n"
        "    meta = Meta()\n"
        "    def validateParams(self, params):\n"
        "        return None\n"
        "    def executeNode(self, inputs, params, runtimeContext):\n"
        "        return {'status': 'ok', 'outputs': {}}\n",
    )
    pluginRoot = tmp_path / "plugins"
    _writeManifest(pluginRoot, "raising_meta", _validManifest(entry=entry))
    monkeypatch.syspath_prepend(str(tmp_path))

    scanResult = PluginRegistry(coreVersion="0.1.0").scan(pluginRoot)

    issues = scanResult.rejectedOperators["vision.demo.temp"]
    assert _codes(issues) == {"E_OPERATOR_VALIDATION_FAILED"}
    assert "broken metadata" in issues[0].message


def testRegistryChecksOperatorIdentityConsistency(tmp_path: Path, monkeypatch) -> None:
    entry = _writeOperatorPackage(
        tmp_path,
        "mismatched_meta_plugin",
        _validOperatorSource(metaOperatorId="vision.demo.other"),
    )
    pluginRoot = tmp_path / "plugins"
    _writeManifest(pluginRoot, "mismatched_meta", _validManifest(entry=entry))
    monkeypatch.syspath_prepend(str(tmp_path))

    scanResult = PluginRegistry(coreVersion="0.1.0").scan(pluginRoot)

    issues = scanResult.rejectedOperators["vision.demo.temp"]
    assert _codes(issues) == {"E_META_OPERATOR_ID_MISMATCH"}


def testVersionCompatibilityUsesFullVersionRange() -> None:
    assert isVersionCompatible("0.2.0", "0.1.0", "0.2.0") is True
    assert isVersionCompatible("0.2.0", "0.3.0", "1.x") is False
    assert isVersionCompatible("1.9.0", "0.1.0", "1.x") is True
    assert isVersionCompatible("1.2.9", "1.2.0", "1.2.x") is True
    assert isVersionCompatible("2.0.0", "0.1.0", "1.x") is False
    assert isVersionCompatible("1.0.0-rc.1", "1.0.0", "1.x") is False
    assert isVersionCompatible("01.0.0", "0.1.0", "1.x") is False
    assert isVersionCompatible("invalid", "0.1.0", "1.x") is False


def _validManifest(
    *,
    entry: str,
    operatorId: str = "vision.demo.temp",
    displayName: str = "Temp",
) -> dict[str, object]:
    return {
        "operatorId": operatorId,
        "displayName": displayName,
        "version": "0.1.0",
        "entry": entry,
        "inputPorts": {},
        "outputPorts": {},
        "paramSchema": {"type": "object"},
        "minCoreVersion": "0.1.0",
        "maxCoreVersion": "1.x",
    }


def _validOperatorSource(
    metaOperatorId: str = "vision.demo.temp",
) -> str:
    return (
        "class TempOperator:\n"
        "    class Meta:\n"
        f"        operatorId = {metaOperatorId!r}\n"
        "        displayName = 'Temp'\n"
        "        version = '0.1.0'\n"
        "        inputPorts = {}\n"
        "        outputPorts = {}\n"
        "        paramSchema = {'type': 'object'}\n"
        "    meta = Meta()\n"
        "    def validateParams(self, params):\n"
        "        return None\n"
        "    def executeNode(self, inputs, params, runtimeContext):\n"
        "        return {'status': 'ok', 'outputs': {}}\n"
    )


def _writeOperatorPackage(tmp_path: Path, packageName: str, source: str) -> str:
    moduleDir = tmp_path / packageName
    moduleDir.mkdir()
    (moduleDir / "__init__.py").write_text("", encoding="utf-8")
    (moduleDir / "operator.py").write_text(source, encoding="utf-8")
    return f"{packageName}.operator:TempOperator"


def _writeManifest(
    pluginRoot: Path,
    directoryName: str,
    payload: dict[str, object],
) -> Path:
    pluginDir = pluginRoot / "builtins" / directoryName
    pluginDir.mkdir(parents=True)
    manifestPath = pluginDir / "manifest.json"
    manifestPath.write_text(json.dumps(payload), encoding="utf-8")
    return manifestPath


def _codes(issues) -> set[str]:
    return {issue.code for issue in issues}
