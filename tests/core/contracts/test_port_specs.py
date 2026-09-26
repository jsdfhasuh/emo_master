import pytest

from emo_master.core.contracts.geometry2d import BBox2D
from emo_master.core.contracts.port_compatibility import (
    arePortTypesCompatible,
    isPortTypeAssignable,
)
from emo_master.core.contracts.port_types import (
    PortSpecValidationError,
    canonicalPortTypes,
    isPortRequired,
    matchesPortSpec,
    normalizePortSpec,
    validatePortSpec,
)
from emo_master.core.graph.validator import validateFlowGraph
from emo_master.core.plugin.validator import (
    validateConsistency,
    validateManifestFields,
)


def testStringPortShorthandRemainsCompatible() -> None:
    assert validatePortSpec("bbox2d") == "bbox2d"
    assert normalizePortSpec("DetectionCollection") == {
        "type": "detectionCollection"
    }
    assert isPortRequired("object") is True
    assert matchesPortSpec(None, "object") is True
    assert canonicalPortTypes({"value": "int"}) == {"value": "integer"}


def testDescriptorControlsRequiredNullableAndPayloadVersion() -> None:
    spec = {
        "type": "bbox2d",
        "required": False,
        "nullable": True,
        "schemaVersion": "1.x",
    }
    payload = BBox2D(1, 2, 3, 4).toPayload()

    assert validatePortSpec(spec) == spec
    assert isPortRequired(spec) is False
    assert matchesPortSpec(None, spec) is True
    assert matchesPortSpec(payload, spec) is True
    assert matchesPortSpec(
        payload, {**spec, "nullable": False, "schemaVersion": "1.0"}
    ) is False
    assert matchesPortSpec(None, {**spec, "nullable": False}) is False


@pytest.mark.parametrize(
    "spec",
    [
        {"type": "bbox2d", "future": True},
        {"type": "bbox2d", "required": 1},
        {"type": "bbox2d", "nullable": "yes"},
        {"type": "bbox2d", "schemaVersion": None},
        {"type": "bbox2d", "schemaVersion": "2.x"},
        {"type": "string", "schemaVersion": "1.1"},
    ],
)
def testInvalidPortDescriptorsAreRejected(spec: dict[str, object]) -> None:
    with pytest.raises(PortSpecValidationError):
        validatePortSpec(spec)


def testCompatibilityAndGraphValidationUnderstandDescriptors() -> None:
    source = {"type": "bbox2d", "schemaVersion": "1.x"}
    target = {"type": "geometry2d", "required": False, "nullable": True}

    assert arePortTypesCompatible(source, target) is True
    assert isPortTypeAssignable(source, target) is True
    result = validateFlowGraph(
        {
            "nodes": [
                {"nodeId": "source", "outputPorts": {"value": source}},
                {"nodeId": "target", "inputPorts": {"value": target}},
            ],
            "edges": [
                {
                    "fromNode": "source",
                    "fromPort": "value",
                    "toNode": "target",
                    "toPort": "value",
                }
            ],
        }
    )
    assert result == {"ok": True, "errors": []}

    assert arePortTypesCompatible(
        {"type": "bbox2d", "schemaVersion": "1.0"},
        {"type": "geometry2d", "schemaVersion": "1.1"},
    ) is False
    assert isPortTypeAssignable(
        {"type": "bbox2d", "schemaVersion": "1.1"},
        {"type": "geometry2d", "schemaVersion": "1.x"},
    ) is True
    assert isPortTypeAssignable(
        {"type": "bbox2d", "schemaVersion": "1.x"},
        {"type": "geometry2d", "schemaVersion": "1.1"},
    ) is False


def testNullableCompatibilityIsDirectional() -> None:
    nullable = {"type": "geometry2d", "nullable": True}
    nonNullable = {"type": "geometry2d", "nullable": False}

    assert arePortTypesCompatible(nullable, nonNullable) is False
    assert isPortTypeAssignable(nullable, nonNullable) is False
    assert arePortTypesCompatible(nonNullable, nullable) is True
    assert isPortTypeAssignable(nonNullable, nullable) is True
    assert arePortTypesCompatible(nullable, "geometry2d") is True


def testManifestAndOperatorMetaPreservePortDescriptors() -> None:
    inputSpec = {
        "type": "geometry2d",
        "required": False,
        "nullable": True,
        "schemaVersion": "1.x",
    }
    manifest, issues = validateManifestFields(
        {
            "operatorId": "vision.demo.port-spec",
            "displayName": "Port Spec",
            "version": "0.1.0",
            "entry": "demo.operator:DemoOperator",
            "inputPorts": {"roi": inputSpec},
            "outputPorts": {
                "detections": {
                    "type": "detectionCollection",
                    "required": True,
                    "schemaVersion": "1.1",
                }
            },
            "paramSchema": {"type": "object"},
            "minCoreVersion": "0.2.0",
            "maxCoreVersion": "1.x",
        }
    )

    assert issues == []
    assert manifest is not None
    assert manifest.inputPorts["roi"] == inputSpec

    class Operator:
        meta = type(
            "Meta",
            (),
            {
                "inputPorts": {"roi": dict(inputSpec)},
                "outputPorts": dict(manifest.outputPorts),
            },
        )()

    assert validateConsistency(manifest, Operator) == []
