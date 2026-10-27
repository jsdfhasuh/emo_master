import json
import zipfile
from pathlib import Path

from emo_master.core.plugin.registry import PluginRegistry
from emo_master.core.project.migration import migrateProjectPayload
from emo_master.core.project.models import ProjectDocument
from emo_master.core.project.runtime_package import buildRuntimePackage, installRuntimePackage
from emo_master.core.project.snapshots import freezeProjectSnapshot
from emo_master.core.workflow.compiler import WorkflowCompiler
from examples.project_tcp_service import sampleProject
from tests.runtime.production_fixture import productionProject, saveDocument
from tests.core.project.test_project_communication import settings


def testPackageAndSnapshotKeepCommunicationPagesVariablesAndResources(tmp_path):
    source = tmp_path / "source"
    old = productionProject(source, autoStart=True, save=False)
    payload = migrateProjectPayload(old.model_dump(), enableCommunication=True)
    from emo_master.core.project.global_variables import VariableDefinition
    payload["globalVariables"] = dict(flag=VariableDefinition(name="flag", type="boolean", initialValue=True).model_dump())
    payload["communication"] = settings().model_dump()
    document = ProjectDocument.model_validate(payload)
    saveDocument(source, document)
    before = (source / "project.json").read_bytes()
    package = buildRuntimePackage(source, tmp_path / "packages")
    assert (source / "project.json").read_bytes() == before
    with zipfile.ZipFile(package) as archive:
        assert json.loads(archive.read("manifest.json"))["projectSchema"] == "2.5"
    installed = installRuntimePackage(package, tmp_path / "installed")
    assert installed.communication == document.communication
    assert installed.presentation == document.presentation and installed.production == document.production
    assert installed.globalVariables == document.globalVariables
    registry = PluginRegistry("0.6.1").scanRoots([Path("src/emo_master/plugins/builtins")]).activeOperators
    manifests = {key: value.manifest for key, value in registry.items()}
    (tmp_path / "site").mkdir()
    first = freezeProjectSnapshot(installed, manifests, mode="debug", resourceRoot=tmp_path / "installed", siteDataRoot=tmp_path / "site")
    installed.communication.tcpServer.port += 1
    second = freezeProjectSnapshot(installed, manifests, mode="debug", resourceRoot=tmp_path / "installed", siteDataRoot=tmp_path / "site")
    assert first.executionRevision != second.executionRevision


def testExampleHasRealSessionToCalculationDependency():
    document = sampleProject()
    registry = PluginRegistry("0.6.1").scanRoots([Path("src/emo_master/plugins/builtins")]).activeOperators
    scan = PluginRegistry("0.6.1").scanRoots([Path("src/emo_master/plugins/builtins")])
    compiled = WorkflowCompiler(registry, deviceTypes=scan.deviceTypes).compile(document)
    for key in ("channel-a", "channel-b"):
        workflow = compiled.workflows[key]
        assert workflow.topologicalOrder.index("session") < workflow.topologicalOrder.index("calculate")
        assert workflow.topologicalOrder.index("calculate") < workflow.topologicalOrder.index("send")
    assert document.schemaVersion == "2.7" and document.communication is None
    server = document.devices.definitions["tcp-server"]
    assert not server.enabled and server.policies["connect"] == "manual"
    for channel in ("channel-a", "channel-b"):
        nodes = {node.nodeId: node for node in document.workflows[channel].nodes}
        assert nodes["session"].deviceBindings["tcp"].deviceId == "tcp-server"
        assert nodes["session"].deviceBindings["tcp"].selectors == {"channelId": channel}
        assert nodes["send"].deviceBindings["tcp"].deviceId == "tcp-server"
    enabled = sampleProject(port=12345, enabled=True, startupPolicy="on_project_run")
    definition = enabled.devices.definitions["tcp-server"]
    assert definition.enabled and definition.connection["port"] == 12345
    assert definition.policies["connect"] == "on_project_run"
