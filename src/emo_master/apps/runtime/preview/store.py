from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import threading
import time
from typing import Any, Mapping
from uuid import uuid4

import cv2
import numpy as np

from emo_master.core.contracts.port_types import normalizePortType


_PROJECT_LIMIT_BYTES = 512 * 1024 * 1024
_GLOBAL_LIMIT_BYTES = 2 * 1024 * 1024 * 1024
_UPLOAD_LIMIT_BYTES = 64 * 1024 * 1024
_SNAPSHOT_TYPES = {
    "image",
    "bbox2d",
    "geometry2d",
    "histogram",
    "integer",
    "number",
    "boolean",
    "string",
}


@dataclass(frozen=True)
class PreviewAsset:
    assetId: str
    path: Path
    mimeType: str
    width: int = 0
    height: int = 0
    projectKey: str = ""
    workflowId: str = ""
    nodeId: str = ""
    port: str = ""
    portType: str = ""
    iterationPath: tuple[int, ...] = ()
    createdAtMs: int = 0


class PreviewSnapshotWriter:
    def __init__(self, jobWorkspace: Path) -> None:
        self.root = jobWorkspace / "preview_staging"
        self.root.mkdir(parents=True, exist_ok=True)
        self._entries: dict[tuple[str, str, str], dict[str, object]] = {}

    def capture(self, node: object, outputs: Mapping[str, object], context: object) -> None:
        outputPorts = getattr(node, "outputPorts", {})
        if not isinstance(outputPorts, Mapping):
            return
        workflowId = str(getattr(context, "workflowId", ""))
        nodeId = str(getattr(node, "nodeId", ""))
        rawIteration = getattr(context, "iterationPath", ())
        iterationPath = [int(item) for item in rawIteration]
        for port, value in outputs.items():
            if port not in outputPorts:
                continue
            portType = normalizePortType(outputPorts[port])
            if portType not in _SNAPSHOT_TYPES:
                continue
            key = (workflowId, nodeId, port)
            identity = hashlib.sha256("\0".join(key).encode("utf-8")).hexdigest()
            createdAtMs = int(time.time() * 1000)
            if portType == "image":
                if not isinstance(value, np.ndarray) or value.dtype != np.uint8:
                    continue
                ok, encoded = cv2.imencode(".png", value)
                if not ok:
                    continue
                relativePath = f"assets/{identity}.png"
                _atomicBytes(self.root / relativePath, encoded.tobytes())
                height, width = value.shape[:2]
                entry: dict[str, object] = {
                    "workflowId": workflowId,
                    "nodeId": nodeId,
                    "port": port,
                    "portType": portType,
                    "relativePath": relativePath,
                    "mimeType": "image/png",
                    "width": int(width),
                    "height": int(height),
                    "iterationPath": iterationPath,
                    "createdAtMs": createdAtMs,
                }
            else:
                if not _isJsonValue(value):
                    continue
                relativePath = f"assets/{identity}.json"
                _atomicJson(self.root / relativePath, value)
                entry = {
                    "workflowId": workflowId,
                    "nodeId": nodeId,
                    "port": port,
                    "portType": portType,
                    "relativePath": relativePath,
                    "mimeType": "application/json",
                    "width": 0,
                    "height": 0,
                    "iterationPath": iterationPath,
                    "createdAtMs": createdAtMs,
                }
            self._entries[key] = entry
        _atomicJson(self.root / "index.json", {"entries": list(self._entries.values())})


class PreviewAssetStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.transientRoot = self.root / "_transient"
        self.transientRoot.mkdir(parents=True, exist_ok=True)
        self._assets: dict[str, PreviewAsset] = {}
        self._lock = threading.RLock()
        self._loadPersistentAssets()

    @staticmethod
    def projectKey(projectPath: str, projectId: str) -> str:
        try:
            normalizedPath = os.path.normcase(str(Path(projectPath).resolve(strict=False)))
        except (OSError, RuntimeError):
            normalizedPath = os.path.normcase(str(Path(projectPath).absolute()))
        return hashlib.sha256(f"{normalizedPath}\0{projectId}".encode("utf-8")).hexdigest()

    def promote(self, stagingRoot: Path, projectKey: str) -> None:
        with self._lock:
            self._promoteLocked(stagingRoot, projectKey)

    def _promoteLocked(self, stagingRoot: Path, projectKey: str) -> None:
        index = _readIndex(stagingRoot / "index.json")
        if not index:
            return
        projectRoot = self.root / projectKey
        assetsRoot = projectRoot / "assets"
        assetsRoot.mkdir(parents=True, exist_ok=True)
        existing = _readIndex(projectRoot / "index.json")
        merged = {
            _entryKey(entry): dict(entry)
            for entry in existing
            if _validEntry(entry)
        }
        for rawEntry in index:
            if not _validEntry(rawEntry):
                continue
            entry = dict(rawEntry)
            source = stagingRoot / str(entry["relativePath"])
            if not source.exists() or not source.is_file():
                continue
            suffix = source.suffix.lower()
            identity = hashlib.sha256(
                (projectKey + "\0" + "\0".join(_entryKey(entry))).encode("utf-8")
            ).hexdigest()
            relativePath = f"assets/{identity}{suffix}"
            destination = projectRoot / relativePath
            _atomicCopy(source, destination)
            old = merged.get(_entryKey(entry))
            if old is not None:
                oldPath = projectRoot / str(old.get("relativePath", ""))
                if oldPath != destination:
                    _unlink(oldPath)
            entry["relativePath"] = relativePath
            merged[_entryKey(entry)] = entry
        _atomicJson(projectRoot / "index.json", {"entries": list(merged.values())})
        self._enforceLimits()
        self._loadPersistentAssets()

    def addUploadedImage(
        self,
        data: bytes,
        filename: str = "",
        *,
        projectKey: str = "",
    ) -> PreviewAsset:
        if not data or len(data) > _UPLOAD_LIMIT_BYTES:
            raise ValueError("preview upload must contain 1 to 67108864 bytes")
        array = np.frombuffer(data, dtype=np.uint8)
        image = cv2.imdecode(array, cv2.IMREAD_UNCHANGED)
        if image is None or image.dtype != np.uint8 or image.ndim not in {2, 3}:
            raise ValueError("preview upload is not a supported uint8 image")
        if image.ndim == 3 and image.shape[2] not in {3, 4}:
            raise ValueError("preview upload must be GRAY, BGR, BGRA or encoded equivalent")
        if image.ndim == 3 and image.shape[2] == 4:
            image = cv2.cvtColor(image, cv2.COLOR_BGRA2BGR)
        return self.addTransientImage(
            image,
            hint=filename or "upload",
            projectKey=projectKey,
        )

    def addTransientImage(
        self,
        image: np.ndarray[Any, Any],
        *,
        hint: str = "preview",
        projectKey: str = "",
    ) -> PreviewAsset:
        if image.dtype != np.uint8 or image.ndim not in {2, 3}:
            raise ValueError("preview image must be uint8 GRAY or BGR")
        ok, encoded = cv2.imencode(".png", image)
        if not ok:
            raise ValueError("failed to encode preview image")
        with self._lock:
            assetId = f"transient-{uuid4()}"
            path = self.transientRoot / f"{assetId}.png"
            _atomicBytes(path, encoded.tobytes())
            height, width = image.shape[:2]
            asset = PreviewAsset(
                assetId=assetId,
                path=path,
                mimeType="image/png",
                width=int(width),
                height=int(height),
                projectKey=projectKey,
                port=hint,
                portType="image",
                createdAtMs=int(time.time() * 1000),
            )
            self._assets[assetId] = asset
            return asset

    def resolve(self, assetId: str) -> PreviewAsset | None:
        with self._lock:
            asset = self._assets.get(assetId)
            if asset is None or not asset.path.exists():
                return None
            return asset

    def isUsableByProject(self, assetId: str, projectKey: str) -> bool:
        """Compatibility check for callers that still create unowned transient assets."""
        with self._lock:
            asset = self._assets.get(assetId)
            return (
                asset is not None
                and asset.path.exists()
                and (asset.projectKey == "" or asset.projectKey == projectKey)
            )

    def isOwnedByProject(self, assetId: str, projectKey: str) -> bool:
        with self._lock:
            asset = self._assets.get(assetId)
            return (
                asset is not None
                and asset.path.exists()
                and projectKey != ""
                and asset.projectKey == projectKey
            )

    def readImage(self, assetId: str) -> np.ndarray[Any, Any]:
        with self._lock:
            asset = self._assets.get(assetId)
            if asset is None or not asset.path.exists() or asset.portType != "image":
                raise KeyError(assetId)
            image = cv2.imread(str(asset.path), cv2.IMREAD_UNCHANGED)
            if image is None or image.dtype != np.uint8:
                raise ValueError("preview image asset is invalid")
            return image

    def readBytes(
        self,
        assetId: str,
        *,
        projectKey: str | None = None,
    ) -> tuple[bytes, str]:
        with self._lock:
            asset = self._assets.get(assetId)
            if (
                asset is None
                or not asset.path.exists()
                or (projectKey is not None and asset.projectKey != projectKey)
            ):
                raise KeyError(assetId)
            return asset.path.read_bytes(), asset.mimeType

    def companionPayload(self, assetId: str) -> dict[str, object] | None:
        with self._lock:
            asset = self._assets.get(assetId)
            if asset is None or not asset.path.exists() or not asset.projectKey:
                return None
            candidates = {
                "image": ("frame",),
                "croppedImage": ("croppedFrame", "frame"),
                "maskedImage": ("maskedFrame", "frame"),
                "mask": ("maskFrame", "frame"),
            }.get(asset.port, ("frame",))
            for candidate in candidates:
                for other in self._assets.values():
                    if (
                        other.projectKey == asset.projectKey
                        and other.workflowId == asset.workflowId
                        and other.nodeId == asset.nodeId
                        and other.port == candidate
                        and other.mimeType == "application/json"
                        and other.path.exists()
                    ):
                        try:
                            payload = json.loads(other.path.read_text(encoding="utf-8"))
                        except (OSError, UnicodeError, json.JSONDecodeError):
                            continue
                        if isinstance(payload, dict):
                            return payload
            return None

    def listSources(
        self,
        projectKey: str,
        workflowId: str,
        nodeId: str,
        incomingEdges: list[tuple[str, str]],
    ) -> list[PreviewAsset]:
        incoming = set(incomingEdges)
        with self._lock:
            result = [
                asset
                for asset in self._assets.values()
                if asset.projectKey == projectKey
                and asset.workflowId == workflowId
                and asset.portType in {"image", "histogram"}
                and (
                    asset.nodeId == nodeId
                    or (asset.nodeId, asset.port) in incoming
                )
            ]
        return sorted(
            result,
            key=lambda item: (
                0 if item.nodeId == nodeId else 1,
                item.nodeId,
                item.port,
            ),
        )

    def removeTransient(self, assetId: str) -> None:
        with self._lock:
            asset = self._assets.get(assetId)
            if asset is None or asset.path.parent != self.transientRoot:
                return
            self._assets.pop(assetId, None)
            _unlink(asset.path)

    def close(self) -> None:
        with self._lock:
            for assetId in list(self._assets):
                self.removeTransient(assetId)

    def _loadPersistentAssets(self) -> None:
        with self._lock:
            transient = {
                key: value
                for key, value in self._assets.items()
                if value.path.parent == self.transientRoot
            }
            loaded: dict[str, PreviewAsset] = {}
            for projectRoot in self.root.iterdir():
                if not projectRoot.is_dir() or projectRoot.name.startswith("_"):
                    continue
                for entry in _readIndex(projectRoot / "index.json"):
                    if not _validEntry(entry):
                        continue
                    key = _entryKey(entry)
                    identity = hashlib.sha256(
                        (projectRoot.name + "\0" + "\0".join(key)).encode("utf-8")
                    ).hexdigest()
                    assetId = f"snapshot-{identity}"
                    relativePath = str(entry["relativePath"])
                    rawIteration = entry.get("iterationPath", [])
                    iteration = (
                        tuple(int(item) for item in rawIteration)
                        if isinstance(rawIteration, list)
                        else ()
                    )
                    loaded[assetId] = PreviewAsset(
                        assetId=assetId,
                        path=projectRoot / relativePath,
                        mimeType=str(entry.get("mimeType", "application/octet-stream")),
                        width=_intValue(entry.get("width", 0)),
                        height=_intValue(entry.get("height", 0)),
                        projectKey=projectRoot.name,
                        workflowId=key[0],
                        nodeId=key[1],
                        port=key[2],
                        portType=str(entry.get("portType", "")),
                        iterationPath=iteration,
                        createdAtMs=_intValue(entry.get("createdAtMs", 0)),
                    )
            self._assets = {**loaded, **transient}

    def _enforceLimits(self) -> None:
        projects: list[tuple[Path, list[dict[str, object]]]] = []
        for projectRoot in self.root.iterdir():
            if not projectRoot.is_dir() or projectRoot.name.startswith("_"):
                continue
            entries = [entry for entry in _readIndex(projectRoot / "index.json") if _validEntry(entry)]
            entries = self._trimEntries(projectRoot, entries, _PROJECT_LIMIT_BYTES)
            _atomicJson(projectRoot / "index.json", {"entries": entries})
            projects.append((projectRoot, entries))
        combined: list[tuple[int, Path, dict[str, object], int]] = []
        total = 0
        for projectRoot, entries in projects:
            for entry in entries:
                path = projectRoot / str(entry["relativePath"])
                size = path.stat().st_size if path.exists() else 0
                total += size
                combined.append(
                    (_intValue(entry.get("createdAtMs", 0)), projectRoot, entry, size)
                )
        for _created, projectRoot, entry, size in sorted(combined, key=lambda item: item[0]):
            if total <= _GLOBAL_LIMIT_BYTES:
                break
            _unlink(projectRoot / str(entry["relativePath"]))
            total -= size
            entries = _readIndex(projectRoot / "index.json")
            entries = [item for item in entries if _entryKey(item) != _entryKey(entry)]
            _atomicJson(projectRoot / "index.json", {"entries": entries})

    @staticmethod
    def _trimEntries(
        projectRoot: Path,
        entries: list[dict[str, object]],
        limit: int,
    ) -> list[dict[str, object]]:
        sizes = []
        total = 0
        for entry in entries:
            path = projectRoot / str(entry["relativePath"])
            size = path.stat().st_size if path.exists() else 0
            sizes.append((entry, path, size))
            total += size
        for entry, path, size in sorted(
            sizes, key=lambda item: _intValue(item[0].get("createdAtMs", 0))
        ):
            if total <= limit:
                break
            _unlink(path)
            total -= size
            entries = [item for item in entries if _entryKey(item) != _entryKey(entry)]
        return entries


def _entryKey(entry: Mapping[str, object]) -> tuple[str, str, str]:
    return (
        str(entry.get("workflowId", "")),
        str(entry.get("nodeId", "")),
        str(entry.get("port", "")),
    )


def _validEntry(entry: object) -> bool:
    return isinstance(entry, dict) and all(
        isinstance(entry.get(key), str) and entry.get(key) != ""
        for key in ("workflowId", "nodeId", "port", "relativePath")
    )


def _readIndex(path: Path) -> list[dict[str, object]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return []
    entries = payload.get("entries") if isinstance(payload, dict) else None
    return [dict(entry) for entry in entries if isinstance(entry, dict)] if isinstance(entries, list) else []


def _isJsonValue(value: object) -> bool:
    try:
        json.dumps(value, allow_nan=False)
        return True
    except (TypeError, ValueError):
        return False


def _intValue(value: object, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    return default


def _atomicJson(path: Path, payload: object) -> None:
    data = (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    _atomicBytes(path, data)


def _atomicBytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temp.open("wb") as fileObj:
            fileObj.write(data)
            fileObj.flush()
            os.fsync(fileObj.fileno())
        temp.replace(path)
    finally:
        _unlink(temp)


def _atomicCopy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
    try:
        shutil.copy2(source, temp)
        temp.replace(destination)
    finally:
        _unlink(temp)


def _unlink(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
