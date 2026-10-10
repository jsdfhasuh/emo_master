"""File substitution must fail closed until it has a portable resource contract."""
import json

import pytest

from emo_master.apps.runtime.preview.global_variables import previewParameters
from emo_master.core.project.global_variables import (
    VariableError, definitions, validateBindings, validateEffectiveParams,
)
from emo_master.core.project.models import ProjectDocument
from emo_master.core.project.runtime_directory import prepareRuntimeProject
from emo_master.core.project.runtime_package import buildRuntimePackage
from emo_master.core.project.test_delivery import trustedRegistry
from emo_master.core.workflow.compiler import WorkflowCompiler
from emo_master.core.workflow.errors import WorkflowCompileError
from tests.runtime.test_global_variables import boundProject, variable


@pytest.mark.parametrize("mode", ["open", "save", "directory"])
@pytest.mark.parametrize("kind", ["constant", "variable"])
def testNestedFileBindingsRejectedBySharedContract(mode, kind):
    schema = {"type": "object", "properties": {"nested": {"type": "object", "properties": {
        "path": {"type": "string", "xWidget": "file", "xFileMode": mode}}}}}
    bindings = [{"parameterPath": ["nested", "path"], "variableId": "v"}]
    variables = definitions({"v": variable("asset.png", "string", kind=kind)})
    with pytest.raises(VariableError) as error:
        validateBindings(bindings, variables, schema)
    assert error.value.code == "E_VARIABLE_FILE_BINDING"
    # Execute-time validation also rejects a file binding, even for a compiled
    # node constructed outside the compiler or whose schema has since changed.
    with pytest.raises(VariableError) as error:
        validateEffectiveParams({"nested": {"path": "asset.png"}}, bindings, schema)
    assert error.value.code == "E_VARIABLE_FILE_BINDING"


def _fileProject(operatorId, field, literal, actual):
    payload = boundProject()
    payload["globalVariables"] = {"v": variable(str(actual), "string", kind="constant")}
    workflow = payload["workflows"]["main"]
    workflow["outputs"] = {}
    workflow["edges"] = []
    workflow["nodes"][1].update(operatorId=operatorId, params={field: str(literal)},
        globalVariableBindings=[{"parameterPath": [field], "variableId": "v"}])
    return ProjectDocument.model_validate(payload)


@pytest.mark.parametrize("operatorId,field", [
    ("vision.io.image_loader", "imagePath"), ("vision.io.image_saver", "outputPath"),
    ("vision.inference.yolo", "modelPath"), ("vision.io.image_batch_loader", "folderPath"),
])
def testCompilerPreviewAndDirectoryRejectFileBindingBeforeLiteralResolution(tmp_path, operatorId, field):
    protected = tmp_path / "protected.png"
    protected.write_bytes(b"unchanged")
    document = _fileProject(operatorId, field, tmp_path / "missing-literal", protected)
    registry = trustedRegistry()
    with pytest.raises(WorkflowCompileError) as error:
        WorkflowCompiler(registry).compile(document)
    assert any(issue.code == "E_VARIABLE_FILE_BINDING" for issue in error.value.issues)
    with pytest.raises(VariableError) as error:
        prepareRuntimeProject(document, tmp_path, registry, protectedPaths=(protected,))
    assert error.value.code == "E_VARIABLE_FILE_BINDING"
    with pytest.raises(VariableError) as error:
        previewParameters(None, document, "main", "number", {field: "safe"},
                          registry[operatorId].manifest.paramSchema)
    assert error.value.code == "E_VARIABLE_FILE_BINDING"
    assert protected.read_bytes() == b"unchanged"


def testExportRejectsFileBindingRatherThanPublishingWrongAssets(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    literal, actual = source / "literal.png", source / "bound.png"
    literal.write_bytes(b"LITERAL")
    actual.write_bytes(b"ACTUAL")
    document = _fileProject("vision.io.image_loader", "imagePath", literal, actual)
    projectFile = source / "project.json"
    original = json.dumps(document.model_dump(mode="json"))
    projectFile.write_text(original, encoding="utf-8")
    with pytest.raises(VariableError) as error:
        buildRuntimePackage(source, tmp_path / "packages")
    assert error.value.code == "E_VARIABLE_FILE_BINDING"
    assert not (tmp_path / "packages").exists()
    assert projectFile.read_text(encoding="utf-8") == original
    assert actual.read_bytes() == b"ACTUAL"


def testOrdinaryStringBindingsRemainSupported():
    schema = {"type": "object", "properties": {"host": {"type": "string"}}}
    bindings = [{"parameterPath": ["host"], "variableId": "host"}]
    validateBindings(bindings, definitions({"host": variable("localhost", "string")}), schema)
    assert validateEffectiveParams({"host": "localhost"}, bindings, schema) == {"host": "localhost"}
