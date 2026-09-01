from __future__ import annotations

from collections.abc import Mapping
import importlib
import re

from emo_master.core.contracts.port_types import (
    PortSpecValidationError,
    normalizePortSpec,
    validatePortSpec,
)
from emo_master.core.plugin.models import (
    OperatorEditorSpec,
    PluginManifest,
    ValidationIssue,
)


_VERSION_PART = r"(?:0|[1-9]\d*)"
_EXACT_VERSION_PATTERN = re.compile(
    rf"^(?P<release>{_VERSION_PART}(?:\.{_VERSION_PART}){{0,2}})$"
)


def isVersionCompatible(
    coreVersion: str,
    minCoreVersion: str,
    maxCoreVersion: str,
) -> bool:
    core = _parseExactVersion(coreVersion)
    minimum = _parseExactVersion(minCoreVersion)
    maximum = _parseMaximumVersion(maxCoreVersion)
    if core is None or minimum is None or maximum is None:
        return False

    maximumValue, maximumIsExclusive = maximum
    if core < minimum:
        return False
    if maximumIsExclusive:
        return core < maximumValue
    return core <= maximumValue


def parseEntry(entry: str) -> tuple[str, str] | None:
    if not isinstance(entry, str) or ":" not in entry:
        return None
    moduleName, className = (part.strip() for part in entry.split(":", 1))
    if not moduleName or not className:
        return None
    return moduleName, className


def validateManifestFields(
    manifestData: dict[str, object],
) -> tuple[PluginManifest | None, list[ValidationIssue]]:
    issues: list[ValidationIssue] = []
    requiredFields = (
        "operatorId",
        "displayName",
        "version",
        "entry",
        "inputPorts",
        "outputPorts",
        "paramSchema",
        "minCoreVersion",
        "maxCoreVersion",
    )
    missingFields = [
        fieldName for fieldName in requiredFields if fieldName not in manifestData
    ]
    for fieldName in missingFields:
        issues.append(
            ValidationIssue(
                "R01",
                "E_MANIFEST_FIELD_MISSING",
                f"missing field: {fieldName}",
            )
        )
    if missingFields:
        return None, issues

    requiredStringFields = (
        "operatorId",
        "displayName",
        "version",
        "entry",
        "minCoreVersion",
        "maxCoreVersion",
    )
    parsedStrings: dict[str, str] = {}
    for fieldName in requiredStringFields:
        rawValue = manifestData[fieldName]
        if not isinstance(rawValue, str):
            issues.append(
                ValidationIssue(
                    "R01",
                    "E_MANIFEST_TYPE_INVALID",
                    f"{fieldName} must be string",
                )
            )
            continue
        value = rawValue.strip()
        if value == "":
            issues.append(
                ValidationIssue(
                    "R01",
                    "E_MANIFEST_VALUE_INVALID",
                    f"{fieldName} must be non-empty",
                )
            )
            continue
        parsedStrings[fieldName] = value

    operatorId = parsedStrings.get("operatorId")
    if operatorId is not None and any(character.isspace() for character in operatorId):
        issues.append(
            ValidationIssue(
                "R01",
                "E_MANIFEST_VALUE_INVALID",
                "operatorId must not contain whitespace",
            )
        )

    optionalStrings: dict[str, str] = {}
    for fieldName, defaultValue in (
        ("category", "其他"),
        ("iconKey", "default"),
        ("summary", ""),
    ):
        rawValue = manifestData.get(fieldName, defaultValue)
        if not isinstance(rawValue, str):
            issues.append(
                ValidationIssue(
                    "R01",
                    "E_MANIFEST_TYPE_INVALID",
                    f"{fieldName} must be string",
                )
            )
            continue
        optionalStrings[fieldName] = rawValue.strip()

    inputPorts = _toPortMap(manifestData["inputPorts"], "inputPorts")
    outputPorts = _toPortMap(manifestData["outputPorts"], "outputPorts")
    paramSchema = _toObjectMap(manifestData["paramSchema"])
    if inputPorts is None:
        issues.append(
            ValidationIssue(
                "R01",
                "E_MANIFEST_TYPE_INVALID",
                "inputPorts must be an object map of valid port specs",
            )
        )
    if outputPorts is None:
        issues.append(
            ValidationIssue(
                "R01",
                "E_MANIFEST_TYPE_INVALID",
                "outputPorts must be an object map of valid port specs",
            )
        )
    if paramSchema is None:
        issues.append(
            ValidationIssue(
                "R01",
                "E_MANIFEST_TYPE_INVALID",
                "paramSchema must be an object map",
            )
        )

    version = parsedStrings.get("version")
    minimum = parsedStrings.get("minCoreVersion")
    maximum = parsedStrings.get("maxCoreVersion")
    if version is not None and _parseExactVersion(version) is None:
        issues.append(
            ValidationIssue(
                "R01",
                "E_MANIFEST_VERSION_INVALID",
                "version must contain one to three numeric components without leading zeroes",
            )
        )
    if minimum is not None and _parseExactVersion(minimum) is None:
        issues.append(
            ValidationIssue(
                "R01",
                "E_MANIFEST_VERSION_INVALID",
                "minCoreVersion must contain one to three numeric components without leading zeroes",
            )
        )
    if maximum is not None and _parseMaximumVersion(maximum) is None:
        issues.append(
            ValidationIssue(
                "R01",
                "E_MANIFEST_VERSION_INVALID",
                "maxCoreVersion must be a numeric version or wildcard such as 1.x",
            )
        )
    parsedMinimum = _parseExactVersion(minimum) if minimum is not None else None
    parsedMaximum = _parseMaximumVersion(maximum) if maximum is not None else None
    if (
        minimum is not None
        and maximum is not None
        and parsedMinimum is not None
        and parsedMaximum is not None
        and not _isVersionRangeValid(minimum, maximum)
    ):
        issues.append(
            ValidationIssue(
                "R01",
                "E_MANIFEST_VERSION_RANGE_INVALID",
                "minCoreVersion must not exceed maxCoreVersion",
            )
        )

    if issues:
        return None, issues
    if inputPorts is None or outputPorts is None or paramSchema is None:
        return None, [
            ValidationIssue(
                "R01",
                "E_MANIFEST_TYPE_INVALID",
                "manifest map fields are invalid",
            )
        ]

    editor, _editorIssues = validateEditorSpec(
        manifestData.get("editor"), parsedStrings["entry"]
    )
    manifest = PluginManifest(
        operatorId=parsedStrings["operatorId"],
        displayName=parsedStrings["displayName"],
        version=parsedStrings["version"],
        entry=parsedStrings["entry"],
        category=optionalStrings["category"],
        iconKey=optionalStrings["iconKey"],
        summary=optionalStrings["summary"],
        inputPorts=inputPorts,
        outputPorts=outputPorts,
        paramSchema=paramSchema,
        minCoreVersion=parsedStrings["minCoreVersion"],
        maxCoreVersion=parsedStrings["maxCoreVersion"],
        editor=editor,
    )
    return manifest, []


def validateEditorSpec(
    rawValue: object,
    operatorEntry: str,
) -> tuple[OperatorEditorSpec | None, list[ValidationIssue]]:
    if rawValue is None:
        return None, []
    if not isinstance(rawValue, Mapping):
        return None, [
            ValidationIssue(
                "R08", "E_EDITOR_SPEC_INVALID", "editor must be an object"
            )
        ]
    allowedFields = {
        "schemaVersion",
        "kind",
        "openMode",
        "uiResource",
        "controllerEntry",
        "fallback",
        "previewMode",
    }
    unknownFields = sorted(
        str(key) for key in rawValue if not isinstance(key, str) or key not in allowedFields
    )
    issues: list[ValidationIssue] = []
    if unknownFields:
        issues.append(
            ValidationIssue(
                "R08",
                "E_EDITOR_SPEC_INVALID",
                "editor contains unknown fields: " + ", ".join(unknownFields),
            )
        )

    defaults = {
        "schemaVersion": "1.0",
        "kind": "customUi",
        "openMode": "window",
        "fallback": "schemaForm",
        "previewMode": "none",
    }
    parsed: dict[str, str] = {}
    for fieldName in (
        "schemaVersion",
        "kind",
        "openMode",
        "uiResource",
        "controllerEntry",
        "fallback",
        "previewMode",
    ):
        rawField = rawValue.get(fieldName, defaults.get(fieldName))
        if not isinstance(rawField, str) or rawField.strip() == "":
            issues.append(
                ValidationIssue(
                    "R08",
                    "E_EDITOR_SPEC_INVALID",
                    f"editor.{fieldName} must be a non-empty string",
                )
            )
            continue
        parsed[fieldName] = rawField.strip()

    expectedValues = {
        "schemaVersion": {"1.0"},
        "kind": {"customUi"},
        "openMode": {"window"},
        "fallback": {"schemaForm"},
        "previewMode": {"none", "pure", "live"},
    }
    for fieldName, allowed in expectedValues.items():
        value = parsed.get(fieldName)
        if value is not None and value not in allowed:
            issues.append(
                ValidationIssue(
                    "R08",
                    "E_EDITOR_SPEC_INVALID",
                    f"editor.{fieldName} must be one of: {', '.join(sorted(allowed))}",
                )
            )

    uiResource = parsed.get("uiResource")
    if uiResource is not None:
        normalized = uiResource.replace("\\", "/")
        parts = normalized.split("/")
        if (
            normalized.startswith("/")
            or re.match(r"^[A-Za-z]:", normalized)
            or any(part in {"", ".", ".."} for part in parts)
            or not normalized.lower().endswith(".ui")
        ):
            issues.append(
                ValidationIssue(
                    "R08",
                    "E_EDITOR_ASSET_INVALID",
                    "editor.uiResource must be a relative .ui path without empty, '.' or '..' segments",
                )
            )
        else:
            parsed["uiResource"] = normalized

    controllerEntry = parsed.get("controllerEntry")
    operatorParsed = parseEntry(operatorEntry)
    controllerParsed = parseEntry(controllerEntry) if controllerEntry is not None else None
    if controllerEntry is not None and controllerParsed is None:
        issues.append(
            ValidationIssue(
                "R08",
                "E_EDITOR_CONTROLLER_INVALID",
                "editor.controllerEntry must use module:Class syntax",
            )
        )
    elif operatorParsed is not None and controllerParsed is not None:
        operatorPackage = operatorParsed[0].rsplit(".", 1)[0]
        controllerPackage = controllerParsed[0].rsplit(".", 1)[0]
        if operatorPackage != controllerPackage:
            issues.append(
                ValidationIssue(
                    "R08",
                    "E_EDITOR_CONTROLLER_INVALID",
                    "editor controller must belong to the same package as the operator entry",
                )
            )

    if issues:
        return None, issues
    return (
        OperatorEditorSpec(
            schemaVersion=parsed["schemaVersion"],
            kind=parsed["kind"],
            openMode=parsed["openMode"],
            uiResource=parsed["uiResource"],
            controllerEntry=parsed["controllerEntry"],
            fallback=parsed["fallback"],
            previewMode=parsed["previewMode"],
        ),
        [],
    )


def _toPortMap(
    rawValue: object,
    path: str,
) -> dict[str, object] | None:
    if not isinstance(rawValue, Mapping):
        return None
    parsed: dict[str, object] = {}
    for key, value in rawValue.items():
        if not isinstance(key, str) or key == "" or key.strip() != key:
            return None
        try:
            parsed[key] = validatePortSpec(value, f"{path}.{key}")
        except PortSpecValidationError:
            return None
    return parsed


def _toObjectMap(rawValue: object) -> dict[str, object] | None:
    if not isinstance(rawValue, Mapping):
        return None
    parsed: dict[str, object] = {}
    for key, value in rawValue.items():
        if not isinstance(key, str):
            return None
        parsed[key] = value
    return parsed


def loadOperatorClass(entry: str) -> tuple[type | None, list[ValidationIssue]]:
    parsedEntry = parseEntry(entry)
    if parsedEntry is None:
        return None, [
            ValidationIssue("R02", "E_ENTRY_INVALID", "entry must be module:Class")
        ]

    moduleName, className = parsedEntry
    try:
        module = importlib.import_module(moduleName)
    except Exception as err:
        return None, [ValidationIssue("R02", "E_ENTRY_IMPORT_FAILED", str(err))]

    if not hasattr(module, className):
        return None, [
            ValidationIssue(
                "R02",
                "E_ENTRY_CLASS_MISSING",
                f"missing class: {className}",
            )
        ]

    operatorClass = getattr(module, className)
    if not isinstance(operatorClass, type):
        return None, [
            ValidationIssue("R03", "E_ENTRY_NOT_CLASS", "entry target is not a class")
        ]

    requiredMethods = ("validateParams", "executeNode")
    missingMethods = [
        methodName
        for methodName in requiredMethods
        if not hasattr(operatorClass, methodName)
    ]
    if missingMethods:
        return None, [
            ValidationIssue(
                "R03",
                "E_OPERATOR_METHOD_MISSING",
                f"missing methods: {','.join(missingMethods)}",
            )
        ]

    nonCallableMethods = [
        methodName
        for methodName in requiredMethods
        if not callable(getattr(operatorClass, methodName))
    ]
    if nonCallableMethods:
        return None, [
            ValidationIssue(
                "R03",
                "E_OPERATOR_METHOD_NOT_CALLABLE",
                f"methods must be callable: {','.join(nonCallableMethods)}",
            )
        ]

    return operatorClass, []


def validateConsistency(
    manifest: PluginManifest,
    operatorClass: type,
) -> list[ValidationIssue]:
    if not hasattr(operatorClass, "meta"):
        return [ValidationIssue("R04", "E_META_MISSING", "operator class missing meta")]

    meta = getattr(operatorClass, "meta")
    issues: list[ValidationIssue] = []
    scalarFields = (
        ("operatorId", manifest.operatorId, "E_META_OPERATOR_ID_MISMATCH"),
        ("displayName", manifest.displayName, "E_META_DISPLAY_NAME_MISMATCH"),
        ("version", manifest.version, "E_META_VERSION_MISMATCH"),
    )
    for fieldName, expectedValue, code in scalarFields:
        actualValue = getattr(meta, fieldName, None)
        if actualValue is not None and actualValue != expectedValue:
            issues.append(
                ValidationIssue(
                    "R04",
                    code,
                    f"manifest {fieldName} mismatch operator meta",
                )
            )

    _validateMetaPortMap(
        issues,
        getattr(meta, "inputPorts", None),
        manifest.inputPorts,
        "inputPorts",
        "E_META_INPUT_INVALID",
        "E_META_INPUT_MISMATCH",
    )
    _validateMetaPortMap(
        issues,
        getattr(meta, "outputPorts", None),
        manifest.outputPorts,
        "outputPorts",
        "E_META_OUTPUT_INVALID",
        "E_META_OUTPUT_MISMATCH",
    )

    rawParamSchema = getattr(meta, "paramSchema", None)
    if rawParamSchema is not None:
        metaParamSchema = _toObjectMap(rawParamSchema)
        if metaParamSchema is None:
            issues.append(
                ValidationIssue(
                    "R04",
                    "E_META_PARAM_SCHEMA_INVALID",
                    "operator meta paramSchema must be an object map",
                )
            )
        elif metaParamSchema != manifest.paramSchema:
            issues.append(
                ValidationIssue(
                    "R04",
                    "E_META_PARAM_SCHEMA_MISMATCH",
                    "manifest paramSchema mismatch operator meta",
                )
            )

    return issues


def _validateMetaPortMap(
    issues: list[ValidationIssue],
    rawValue: object,
    expectedValue: dict[str, object],
    fieldName: str,
    invalidCode: str,
    mismatchCode: str,
) -> None:
    if rawValue is None:
        return
    parsedValue = _toPortMap(rawValue, fieldName)
    if parsedValue is None:
        issues.append(
            ValidationIssue(
                "R04",
                invalidCode,
                f"operator meta {fieldName} must be an object map of valid port specs",
            )
        )
    elif {
        name: normalizePortSpec(spec) for name, spec in parsedValue.items()
    } != {
        name: normalizePortSpec(spec) for name, spec in expectedValue.items()
    }:
        issues.append(
            ValidationIssue(
                "R04",
                mismatchCode,
                f"manifest {fieldName} mismatch operator meta",
            )
        )


def _parseExactVersion(value: str) -> tuple[int, int, int] | None:
    if not isinstance(value, str):
        return None
    match = _EXACT_VERSION_PATTERN.fullmatch(value.strip())
    if match is None:
        return None
    parts = [int(part) for part in match.group("release").split(".")]
    parts.extend([0] * (3 - len(parts)))
    return parts[0], parts[1], parts[2]


def _parseMaximumVersion(
    value: str,
) -> tuple[tuple[int, int, int], bool] | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if normalized.endswith(".x"):
        prefix = normalized.split(".")[:-1]
        if len(prefix) not in {1, 2} or any(
            not _isValidVersionPart(part) for part in prefix
        ):
            return None
        numbers = [int(part) for part in prefix]
        if len(numbers) == 1:
            return (numbers[0] + 1, 0, 0), True
        return (numbers[0], numbers[1] + 1, 0), True

    exact = _parseExactVersion(normalized)
    if exact is None:
        return None
    return exact, False


def _isVersionRangeValid(minimum: str, maximum: str) -> bool:
    minimumValue = _parseExactVersion(minimum)
    maximumResult = _parseMaximumVersion(maximum)
    if minimumValue is None or maximumResult is None:
        return False
    maximumValue, maximumIsExclusive = maximumResult
    if maximumIsExclusive:
        return minimumValue < maximumValue
    return minimumValue <= maximumValue


def _isValidVersionPart(value: str) -> bool:
    return value.isdigit() and (value == "0" or not value.startswith("0"))
