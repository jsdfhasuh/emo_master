from pathlib import Path

from emo_master.core.plugin.registry import PluginRegistry


def testRegistryLoadsValidManifest(tmp_path: Path, monkeypatch) -> None:
  pluginRoot = tmp_path / "plugins"
  pluginDir = pluginRoot / "builtins" / "temp_edge"
  pluginDir.mkdir(parents=True)

  moduleDir = tmp_path / "temp_plugin"
  moduleDir.mkdir()
  (moduleDir / "__init__.py").write_text("")
  (moduleDir / "operator.py").write_text(
    "class TempOperator:\n"
    "  class Meta:\n"
    "    inputPorts = {}\n"
    "    outputPorts = {}\n"
    "  meta = Meta()\n"
    "  def validateParams(self, params):\n"
    "    return None\n"
    "  def executeNode(self, inputs, params, runtimeContext):\n"
    "    return {'status': 'ok', 'outputs': {}, 'metrics': {'latencyMs': 0}, 'diagnostics': {'text': 'ok'}}\n"
  )

  (pluginDir / "manifest.json").write_text(
    "{"
    "\"operatorId\":\"vision.demo.temp\","
    "\"displayName\":\"Temp\","
    "\"version\":\"0.1.0\","
    "\"entry\":\"temp_plugin.operator:TempOperator\","
    "\"inputPorts\":{},"
    "\"outputPorts\":{},"
    "\"paramSchema\":{\"type\":\"object\"},"
    "\"minCoreVersion\":\"0.1.0\","
    "\"maxCoreVersion\":\"1.x\""
    "}"
  )

  monkeypatch.syspath_prepend(str(tmp_path))
  registry = PluginRegistry(coreVersion="0.1.0")
  scanResult = registry.scan(pluginRoot)

  assert "vision.demo.temp" in scanResult.activeOperators
  assert scanResult.rejectedOperators == {}


def testRegistryRejectsInvalidEntry(tmp_path: Path) -> None:
  pluginRoot = tmp_path / "plugins"
  pluginDir = pluginRoot / "builtins" / "bad_edge"
  pluginDir.mkdir(parents=True)

  (pluginDir / "manifest.json").write_text(
    "{"
    "\"operatorId\":\"vision.demo.bad\","
    "\"displayName\":\"Bad\","
    "\"version\":\"0.1.0\","
    "\"entry\":\"bad.module:MissingClass\","
    "\"inputPorts\":{},"
    "\"outputPorts\":{},"
    "\"paramSchema\":{\"type\":\"object\"},"
    "\"minCoreVersion\":\"0.1.0\","
    "\"maxCoreVersion\":\"1.x\""
    "}"
  )

  registry = PluginRegistry(coreVersion="0.1.0")
  scanResult = registry.scan(pluginRoot)

  assert "vision.demo.bad" in scanResult.rejectedOperators
  assert "vision.demo.bad" not in scanResult.activeOperators
