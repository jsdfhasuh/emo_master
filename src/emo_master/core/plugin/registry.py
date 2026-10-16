from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import json
import hashlib
import os
from pathlib import Path
import xml.etree.ElementTree as ElementTree

from emo_master.core.plugin.models import (
    OperatorEditorSpec,
    PluginDescriptor,
    PluginManifest,
    RegistryScanResult,
    ValidationIssue,
)
from emo_master.core.plugin.icon_resources import (
    IconValidationError, MAX_ICON_TOTAL_BYTES, loadIconAsset, parseIconResource,
)
from emo_master.core.plugin.validator import (
    isVersionCompatible,
    loadOperatorClass,
    validateConsistency,
    validateEditorSpec,
    validateManifestFields,
)


_MAX_EDITOR_UI_BYTES = 2 * 1024 * 1024
_ALLOWED_EDITOR_WIDGETS = {
    "QWidget",
    "QFrame",
    "QLabel",
    "QPushButton",
    "QToolButton",
    "QLineEdit",
    "QTextEdit",
    "QPlainTextEdit",
    "QCheckBox",
    "QRadioButton",
    "QComboBox",
    "QSpinBox",
    "QDoubleSpinBox",
    "QSlider",
    "QDial",
    "QProgressBar",
    "QGroupBox",
    "QTabWidget",
    "QStackedWidget",
    "QScrollArea",
    "QListWidget",
    "QTreeWidget",
    "QTableWidget",
    "QDateEdit",
    "QTimeEdit",
    "QDateTimeEdit",
}


@dataclass(frozen=True)
class _ManifestRecord:
    path: Path
    manifest: PluginManifest | None
    issues: tuple[ValidationIssue, ...]
    rawData: dict[str, object]


class PluginRegistry:
    def __init__(self, coreVersion: str) -> None:
        self.coreVersion = coreVersion

    def scan(self, pluginRoot: Path) -> RegistryScanResult:
        return self.scanRoots((pluginRoot,))

    def scanRoots(self, pluginRoots: Iterable[Path | str]) -> RegistryScanResult:
        activeOperators: dict[str, PluginDescriptor] = {}
        manifestPaths, rejectedOperators = self._discoverManifestPaths(pluginRoots)
        recordsByOperatorId: dict[str, list[_ManifestRecord]] = {}
        iconBytesRemaining = MAX_ICON_TOTAL_BYTES

        for manifestPath in manifestPaths:
            manifestData, loadIssue = self._readManifest(manifestPath)
            if manifestData is None:
                issue = loadIssue or ValidationIssue(
                    "R01",
                    "E_MANIFEST_PARSE_FAILED",
                    "manifest json parse failed",
                )
                self._appendIssues(
                    rejectedOperators,
                    str(manifestPath),
                    self._withSource((issue,), manifestPath),
                )
                continue

            manifest, fieldIssues = validateManifestFields(manifestData)
            declaredOperatorId = self._declaredOperatorId(manifestData)
            record = _ManifestRecord(
                path=manifestPath,
                manifest=manifest,
                issues=tuple(fieldIssues),
                rawData=manifestData,
            )
            if declaredOperatorId is None:
                self._appendIssues(
                    rejectedOperators,
                    str(manifestPath),
                    self._withSource(record.issues, manifestPath),
                )
                continue
            recordsByOperatorId.setdefault(declaredOperatorId, []).append(record)

        for operatorId, records in recordsByOperatorId.items():
            if len(records) > 1:
                sourcePaths = ", ".join(str(record.path) for record in records)
                duplicateIssues = [
                    ValidationIssue(
                        "R01",
                        "E_OPERATOR_ID_DUPLICATED",
                        f"operatorId '{operatorId}' is declared by multiple manifests: {sourcePaths}",
                    )
                ]
                for record in records:
                    duplicateIssues.extend(self._withSource(record.issues, record.path))
                self._appendIssues(rejectedOperators, operatorId, duplicateIssues)
                continue

            record = records[0]
            if record.manifest is None:
                self._appendIssues(
                    rejectedOperators,
                    operatorId,
                    self._withSource(record.issues, record.path),
                )
                continue

            manifest = record.manifest
            issues: list[ValidationIssue] = list(record.issues)
            rawManifest = record.rawData
            editor, editorIssues = validateEditorSpec(
                rawManifest.get("editor"), manifest.entry
            )
            editorUiContent: bytes | None = None
            editorUiSha256 = ""
            if editor is not None:
                resourceIssues = self._validateEditorResource(
                    editor, record.path.parent
                )
                editorIssues.extend(resourceIssues)
                if not resourceIssues:
                    try:
                        resourcePath = record.path.parent.joinpath(
                            *editor.uiResource.split("/")
                        ).resolve(strict=True)
                        editorUiContent = resourcePath.read_bytes()
                        editorUiSha256 = hashlib.sha256(editorUiContent).hexdigest()
                    except OSError as err:
                        editorIssues.append(
                            ValidationIssue(
                                "R08",
                                "E_EDITOR_ASSET_NOT_FOUND",
                                f"editor UI resource became unavailable: {err}",
                            )
                        )
            if not isVersionCompatible(
                self.coreVersion,
                manifest.minCoreVersion,
                manifest.maxCoreVersion,
            ):
                issues.append(
                    ValidationIssue(
                        "R07",
                        "E_CORE_VERSION_INCOMPATIBLE",
                        "core version "
                        f"{self.coreVersion} is outside supported range "
                        f"[{manifest.minCoreVersion}, {manifest.maxCoreVersion}]",
                    )
                )

            operatorClass: type | None = None
            try:
                operatorClass, classIssues = loadOperatorClass(manifest.entry)
                issues.extend(classIssues)
                if operatorClass is not None:
                    issues.extend(validateConsistency(manifest, operatorClass))
            except Exception as err:
                issues.append(
                    ValidationIssue(
                        "R04",
                        "E_OPERATOR_VALIDATION_FAILED",
                        f"operator validation raised {type(err).__name__}: {err}",
                    )
                )

            if issues:
                self._appendIssues(
                    rejectedOperators,
                    operatorId,
                    self._withSource(issues, record.path),
                )
                continue

            if operatorClass is None:
                self._appendIssues(
                    rejectedOperators,
                    operatorId,
                    self._withSource(
                        (
                            ValidationIssue(
                                "R02",
                                "E_ENTRY_IMPORT_FAILED",
                                "operator class not resolved",
                            ),
                        ),
                        record.path,
                    ),
                )
                continue

            resource, iconIssues = parseIconResource(rawManifest)
            iconAsset = None
            if resource and not iconIssues:
                try:
                    iconAsset = loadIconAsset(record.path.parent, resource, iconBytesRemaining)
                    iconBytesRemaining -= len(iconAsset.content)
                except IconValidationError as err:
                    iconIssues = (err.issue(),)

            activeOperators[operatorId] = PluginDescriptor(
                manifest=manifest,
                operatorClass=operatorClass,
                resourceRoot=record.path.parent,
                editorIssues=tuple(editorIssues),
                editorUiContent=editorUiContent,
                editorUiSha256=editorUiSha256,
                iconAsset=iconAsset,
                iconIssues=iconIssues,
            )

        return RegistryScanResult(
            activeOperators=activeOperators,
            rejectedOperators=rejectedOperators,
        )

    def _discoverManifestPaths(
        self,
        pluginRoots: Iterable[Path | str],
    ) -> tuple[list[Path], dict[str, list[ValidationIssue]]]:
        manifestPaths: list[Path] = []
        rejectedOperators: dict[str, list[ValidationIssue]] = {}
        seenRoots: set[str] = set()
        seenManifests: set[str] = set()

        for rawRoot in pluginRoots:
            root = Path(rawRoot)
            rootIdentity = self._pathIdentity(root)
            if rootIdentity in seenRoots:
                continue
            seenRoots.add(rootIdentity)

            if not root.exists():
                rejectedOperators[str(root)] = [
                    ValidationIssue(
                        "R00",
                        "E_PLUGIN_ROOT_NOT_FOUND",
                        f"plugin root not found: {root}",
                    )
                ]
                continue
            if not root.is_dir():
                rejectedOperators[str(root)] = [
                    ValidationIssue(
                        "R00",
                        "E_PLUGIN_ROOT_NOT_DIRECTORY",
                        f"plugin root is not a directory: {root}",
                    )
                ]
                continue

            try:
                discovered = sorted(root.glob("**/manifest.json"))
            except OSError as err:
                rejectedOperators[str(root)] = [
                    ValidationIssue(
                        "R00",
                        "E_PLUGIN_ROOT_SCAN_FAILED",
                        f"failed to scan plugin root {root}: {err}",
                    )
                ]
                continue

            for manifestPath in discovered:
                manifestIdentity = self._pathIdentity(manifestPath)
                if manifestIdentity in seenManifests:
                    continue
                seenManifests.add(manifestIdentity)
                manifestPaths.append(manifestPath)

        return manifestPaths, rejectedOperators

    def _readManifest(
        self,
        manifestPath: Path,
    ) -> tuple[dict[str, object] | None, ValidationIssue | None]:
        try:
            with manifestPath.open("r", encoding="utf-8") as fileObj:
                parsed = json.load(fileObj)
        except json.JSONDecodeError as err:
            return None, ValidationIssue(
                "R01",
                "E_MANIFEST_PARSE_FAILED",
                f"invalid JSON at line {err.lineno}, column {err.colno}: {err.msg}",
            )
        except (OSError, UnicodeError) as err:
            return None, ValidationIssue(
                "R01",
                "E_MANIFEST_READ_FAILED",
                str(err),
            )
        except Exception as err:
            return None, ValidationIssue(
                "R01",
                "E_MANIFEST_PARSE_FAILED",
                str(err),
            )

        if not isinstance(parsed, dict):
            return None, ValidationIssue(
                "R01",
                "E_MANIFEST_TYPE_INVALID",
                "manifest root must be an object",
            )
        return parsed, None

    def _loadManifestData(self, manifestPath: Path) -> dict[str, object] | None:
        manifestData, _ = self._readManifest(manifestPath)
        return manifestData

    def _validateEditorResource(
        self,
        editor: OperatorEditorSpec,
        resourceRoot: Path,
    ) -> list[ValidationIssue]:
        resourcePath = resourceRoot.joinpath(*editor.uiResource.split("/"))
        try:
            resolvedRoot = resourceRoot.resolve(strict=True)
            resolvedPath = resourcePath.resolve(strict=True)
            resolvedPath.relative_to(resolvedRoot)
        except (OSError, RuntimeError, ValueError) as err:
            return [
                ValidationIssue(
                    "R08",
                    "E_EDITOR_ASSET_NOT_FOUND",
                    f"editor UI resource is unavailable: {err}",
                )
            ]
        if not resolvedPath.is_file():
            return [
                ValidationIssue(
                    "R08",
                    "E_EDITOR_ASSET_NOT_FOUND",
                    "editor UI resource must be a file",
                )
            ]
        try:
            size = resolvedPath.stat().st_size
            if size > _MAX_EDITOR_UI_BYTES:
                return [
                    ValidationIssue(
                        "R08",
                        "E_EDITOR_ASSET_INVALID",
                        f"editor UI resource exceeds {_MAX_EDITOR_UI_BYTES} bytes",
                    )
                ]
            xmlRoot = ElementTree.fromstring(resolvedPath.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ElementTree.ParseError) as err:
            return [
                ValidationIssue(
                    "R08",
                    "E_EDITOR_ASSET_INVALID",
                    f"editor UI resource is invalid: {err}",
                )
            ]
        if xmlRoot.tag != "ui":
            return [
                ValidationIssue(
                    "R08",
                    "E_EDITOR_ASSET_INVALID",
                    "editor UI root element must be <ui>",
                )
            ]
        if xmlRoot.find("customwidgets") is not None:
            return [
                ValidationIssue(
                    "R08",
                    "E_EDITOR_ASSET_INVALID",
                    "editor UI custom widgets are not supported",
                )
            ]
        widgetClasses = {
            str(widget.get("class", "")) for widget in xmlRoot.iter("widget")
        }
        unsupportedWidgets = sorted(widgetClasses - _ALLOWED_EDITOR_WIDGETS)
        if unsupportedWidgets:
            return [
                ValidationIssue(
                    "R08",
                    "E_EDITOR_ASSET_INVALID",
                    "editor UI uses unsupported widget classes: "
                    + ", ".join(unsupportedWidgets),
                )
            ]
        return []

    def _declaredOperatorId(self, manifestData: dict[str, object]) -> str | None:
        rawOperatorId = manifestData.get("operatorId")
        if not isinstance(rawOperatorId, str):
            return None
        operatorId = rawOperatorId.strip()
        return operatorId or None

    def _pathIdentity(self, path: Path) -> str:
        try:
            resolved = path.resolve(strict=False)
        except (OSError, RuntimeError):
            resolved = path.absolute()
        return os.path.normcase(str(resolved))

    def _withSource(
        self,
        issues: Iterable[ValidationIssue],
        manifestPath: Path,
    ) -> list[ValidationIssue]:
        return [
            ValidationIssue(
                issue.ruleId,
                issue.code,
                f"{manifestPath}: {issue.message}",
            )
            for issue in issues
        ]

    def _appendIssues(
        self,
        rejectedOperators: dict[str, list[ValidationIssue]],
        key: str,
        issues: Iterable[ValidationIssue],
    ) -> None:
        rejectedOperators.setdefault(key, []).extend(issues)
