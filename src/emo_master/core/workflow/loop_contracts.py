from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from emo_master.core.contracts.port_compatibility import (
    arePortTypesCompatible,
    isPortTypeAssignable,
)
from emo_master.core.contracts.port_types import listPortType, normalizePortType


LEGACY_LOOP_CONTRACT_VERSION = 1
CURRENT_LOOP_CONTRACT_VERSION = 2


@dataclass(frozen=True)
class LoopContractIssue:
    code: str
    message: str
    fieldPath: str = "loop"


@dataclass(frozen=True)
class LoopContract:
    mode: str
    contractVersion: int
    inputPorts: dict[str, str]
    outputPorts: dict[str, str]
    normalizedConfig: dict[str, object]
    issues: tuple[LoopContractIssue, ...] = ()


def interfacePortTypes(values: Mapping[str, object]) -> dict[str, str]:
    return {
        name: normalizePortType(value)
        for name, value in values.items()
        if isinstance(name, str)
    }


def deriveLoopContract(
    config: Mapping[str, object],
    bodyInputs: Mapping[str, object],
    bodyOutputs: Mapping[str, object],
    conditionInputs: Mapping[str, object] | None = None,
    conditionOutputs: Mapping[str, object] | None = None,
) -> LoopContract:
    normalizedConfig = dict(config)
    issues: list[LoopContractIssue] = []
    modeValue = config.get("mode")
    mode = modeValue if isinstance(modeValue, str) else ""
    versionValue = config.get("contractVersion", LEGACY_LOOP_CONTRACT_VERSION)
    if (
        not isinstance(versionValue, int)
        or isinstance(versionValue, bool)
        or versionValue not in {
            LEGACY_LOOP_CONTRACT_VERSION,
            CURRENT_LOOP_CONTRACT_VERSION,
        }
    ):
        issues.append(
            LoopContractIssue(
                "E_LOOP_CONTRACT_VERSION_INVALID",
                "loop contractVersion must be 1 or 2",
                "loop.contractVersion",
            )
        )
        version = LEGACY_LOOP_CONTRACT_VERSION
    else:
        version = versionValue
    normalizedConfig["contractVersion"] = version

    inputs = interfacePortTypes(bodyInputs)
    outputs = interfacePortTypes(bodyOutputs)
    if mode not in {"repeat", "foreach", "while"}:
        issues.append(
            LoopContractIssue(
                "E_LOOP_MODE_INVALID",
                "loop mode must be repeat, foreach or while",
                "loop.mode",
            )
        )
        return LoopContract(
            mode,
            version,
            {},
            {},
            normalizedConfig,
            tuple(issues),
        )

    if mode == "repeat":
        repeatCount = config.get("repeatCount")
        maximum = config.get("maxIterations")
        if (
            isinstance(repeatCount, int)
            and not isinstance(repeatCount, bool)
            and isinstance(maximum, int)
            and not isinstance(maximum, bool)
            and repeatCount > maximum
        ):
            issues.append(
                LoopContractIssue(
                    "E_LOOP_REPEAT_COUNT_EXCEEDS_LIMIT",
                    "repeatCount must not exceed maxIterations",
                    "loop.repeatCount",
                )
            )
        _validateRepeatZero(config, inputs, outputs, issues)
        return LoopContract(
            mode,
            version,
            inputs,
            outputs,
            normalizedConfig,
            tuple(issues),
        )

    if version == LEGACY_LOOP_CONTRACT_VERSION:
        if mode == "foreach":
            return LoopContract(
                mode,
                version,
                {"items": "list<any>"},
                {"results": "list<any>"},
                normalizedConfig,
                tuple(issues),
            )
        return LoopContract(
            mode,
            version,
            {"state": "object"},
            {"state": "object"},
            normalizedConfig,
            tuple(issues),
        )

    if mode == "foreach":
        return _deriveForEachV2(
            normalizedConfig,
            inputs,
            outputs,
            issues,
        )
    return _deriveWhileV2(
        normalizedConfig,
        inputs,
        outputs,
        interfacePortTypes(conditionInputs or {}),
        interfacePortTypes(conditionOutputs or {}),
        issues,
    )


def _deriveForEachV2(
    config: dict[str, object],
    bodyInputs: dict[str, str],
    bodyOutputs: dict[str, str],
    issues: list[LoopContractIssue],
) -> LoopContract:
    itemPortValue = config.get("itemInputPort")
    itemPort = itemPortValue if isinstance(itemPortValue, str) and itemPortValue else None
    if itemPort is None:
        if "item" in bodyInputs:
            itemPort = "item"
        elif bodyInputs:
            itemPort = next(iter(bodyInputs))
    if itemPort is not None and itemPort not in bodyInputs and len(bodyInputs) == 1:
        itemPort = next(iter(bodyInputs))
    if itemPort is not None:
        config["itemInputPort"] = itemPort
        if itemPort not in bodyInputs:
            issues.append(
                LoopContractIssue(
                    "E_LOOP_ITEM_PORT_UNKNOWN",
                    f"ForEach item input does not exist in body workflow: {itemPort}",
                    "loop.itemInputPort",
                )
            )

    indexPortValue = config.get("indexInputPort")
    indexPort = (
        indexPortValue
        if isinstance(indexPortValue, str) and indexPortValue.strip() != ""
        else None
    )
    if indexPort is None:
        config.pop("indexInputPort", None)
    else:
        config["indexInputPort"] = indexPort
        indexType = bodyInputs.get(indexPort)
        if indexType is None:
            issues.append(
                LoopContractIssue(
                    "E_LOOP_INDEX_PORT_UNKNOWN",
                    f"ForEach index input does not exist in body workflow: {indexPort}",
                    "loop.indexInputPort",
                )
            )
        elif normalizePortType(indexType) != "integer":
            issues.append(
                LoopContractIssue(
                    "E_LOOP_INDEX_PORT_TYPE",
                    "ForEach index input must have type integer",
                    "loop.indexInputPort",
                )
            )

    itemType = bodyInputs.get(itemPort, "any") if itemPort is not None else "any"
    publicInputs: dict[str, str] = {"items": listPortType(itemType)}
    excluded = {name for name in (itemPort, indexPort) if name is not None}
    for name, portType in bodyInputs.items():
        if name in excluded:
            continue
        if name == "items":
            issues.append(
                LoopContractIssue(
                    "E_LOOP_PORT_NAME_COLLISION",
                    "ForEach body shared input conflicts with reserved loop port: items",
                    "loop.bodyWorkflowId",
                )
            )
            continue
        publicInputs[name] = portType
    publicOutputs = {
        name: listPortType(portType) for name, portType in bodyOutputs.items()
    }
    return LoopContract(
        "foreach",
        CURRENT_LOOP_CONTRACT_VERSION,
        publicInputs,
        publicOutputs,
        config,
        tuple(issues),
    )


def _deriveWhileV2(
    config: dict[str, object],
    bodyInputs: dict[str, str],
    bodyOutputs: dict[str, str],
    conditionInputs: dict[str, str],
    conditionOutputs: dict[str, str],
    issues: list[LoopContractIssue],
) -> LoopContract:
    for name, inputType in bodyInputs.items():
        outputType = bodyOutputs.get(name)
        if outputType is None:
            issues.append(
                LoopContractIssue(
                    "E_LOOP_STATE_OUTPUT_MISSING",
                    f"While body must return state port: {name}",
                    "loop.bodyWorkflowId",
                )
            )
        elif not (
            arePortTypesCompatible(outputType, inputType)
            and arePortTypesCompatible(inputType, outputType)
        ):
            issues.append(
                LoopContractIssue(
                    "E_LOOP_STATE_TYPE_MISMATCH",
                    f"While state port type changes across iterations: {name} "
                    f"({inputType} -> {outputType})",
                    "loop.bodyWorkflowId",
                )
            )
    for name in bodyOutputs:
        if name not in bodyInputs:
            issues.append(
                LoopContractIssue(
                    "E_LOOP_STATE_INPUT_MISSING",
                    f"While body output has no matching state input: {name}",
                    "loop.bodyWorkflowId",
                )
            )
    for name, conditionType in conditionInputs.items():
        stateType = bodyInputs.get(name)
        if stateType is None:
            issues.append(
                LoopContractIssue(
                    "E_LOOP_CONDITION_STATE_UNKNOWN",
                    f"While condition input is not part of body state: {name}",
                    "loop.conditionWorkflowId",
                )
            )
        elif not arePortTypesCompatible(stateType, conditionType):
            issues.append(
                LoopContractIssue(
                    "E_LOOP_CONDITION_STATE_TYPE",
                    f"While condition input type is incompatible: {name}",
                    "loop.conditionWorkflowId",
                )
            )
    if conditionOutputs.get("continue") != "boolean":
        issues.append(
            LoopContractIssue(
                "E_LOOP_CONDITION_OUTPUT_INVALID",
                "While condition workflow must output continue:boolean",
                "loop.conditionWorkflowId",
            )
        )
    maximum = config.get("maxIterations")
    if maximum == 0:
        issues.append(
            LoopContractIssue(
                "E_LOOP_LIMIT_INVALID",
                "While maxIterations must be at least 1",
                "loop.maxIterations",
            )
        )
    return LoopContract(
        "while",
        CURRENT_LOOP_CONTRACT_VERSION,
        bodyInputs,
        bodyOutputs,
        config,
        tuple(issues),
    )


def _validateRepeatZero(
    config: Mapping[str, object],
    inputs: Mapping[str, str],
    outputs: Mapping[str, str],
    issues: list[LoopContractIssue],
) -> None:
    if config.get("repeatCount") != 0:
        return
    impossible: list[str] = []
    for name, outputType in outputs.items():
        inputType = inputs.get(name)
        if inputType is None or not isPortTypeAssignable(inputType, outputType):
            impossible.append(name)
    if impossible:
        issues.append(
            LoopContractIssue(
                "E_LOOP_ZERO_OUTPUT_UNSATISFIABLE",
                "repeatCount=0 cannot produce body outputs: " + ", ".join(sorted(impossible)),
                "loop.repeatCount",
            )
        )
