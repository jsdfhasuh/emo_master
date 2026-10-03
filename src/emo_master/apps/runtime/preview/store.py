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


def _ioPath(path: Path) -> Path:
    """Use Win32 extended paths only inside the private preview file store."""
    if os.name != "nt":
        return path
    absolute = os.path.abspath(path)
    if absolute.startswith("\\\\?\\"):
        return Path(absolute)
    if absolute.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + absolute[2:])
    return Path("\\\\?\\" + absolute)


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
    originJobId: str = ""
    originProjectRevision: int = 0
    captureId: str = ""


class PreviewSnapshotWriter:
    def __init__(self, jobWorkspace: Path, *, jobId: str = "", projectRevision: int = 0) -> None:
        self.jobId = jobId
        self.projectRevision = projectRevision
        self.root = _ioPath(jobWorkspace / "preview_staging")
        self.root.mkdir(parents=True, exist_ok=True)
        self._entries: dict[tuple[str, str, str], dict[str, object]] = {}
        self._pendingCleanup: set[Path] = set()
        self._cleanupScanRequired = False

    def _retirePending(self) -> bool:
        if self._cleanupScanRequired:
            try:
                self._pendingCleanup.update(self.root.glob(".index.json.*.tmp"))
                self._pendingCleanup.update((self.root / "assets").glob(".*.tmp"))
            except OSError:
                return False
            self._cleanupScanRequired = False
        return _retirePaths(self._pendingCleanup)

    def capture(self, node: object, outputs: Mapping[str, object], context: object) -> None:
        # A failed unlink cannot become an unbounded UUID-file history. Keep
        # the last committed capture readable, and refuse new allocations until
        # the bounded previous invocation's cleanup actually retires.
        if not self._retirePending():
            raise OSError("legacy snapshot cleanup pending; new capture skipped")
        outputPorts = getattr(node, "outputPorts", {})
        if not isinstance(outputPorts, Mapping):
            return
        workflowId = str(getattr(context, "workflowId", ""))
        nodeId = str(getattr(node, "nodeId", ""))
        rawIteration = getattr(context, "iterationPath", ())
        iterationPath = [int(item) for item in rawIteration]
        # One capture invocation owns all its image/companion ports. Node IDs
        # alone cannot distinguish repeated calls, iterations or later Jobs.
        captureId = str(uuid4())
        originJobId = self.jobId or str(getattr(context, "jobId", ""))
        nextEntries = dict(self._entries)
        created: list[Path] = []
        obsolete: list[Path] = []
        try:
            for port, value in outputs.items():
                if port not in outputPorts:
                    continue
                portType = normalizePortType(outputPorts[port])
                if portType not in _SNAPSHOT_TYPES:
                    continue
                key = (workflowId, nodeId, port)
                identity = hashlib.sha256(("\0".join(key) + "\0" + captureId).encode("utf-8")).hexdigest()
                createdAtMs = int(time.time() * 1000)
                if portType == "image":
                    if not isinstance(value, np.ndarray) or value.dtype != np.uint8:
                        continue
                    ok, encoded = cv2.imencode(".png", value)
                    if not ok:
                        continue
                    relativePath = f"assets/{identity}.png"
                    created.append(self.root / relativePath)
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
                    created.append(self.root / relativePath)
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
                entry.update(originJobId=originJobId, originProjectRevision=self.projectRevision,
                             captureId=captureId)
                previous = nextEntries.get(key)
                if previous is not None:
                    obsolete.append(self.root / str(previous["relativePath"]))
                nextEntries[key] = entry
            _atomicJson(self.root / "index.json", {"entries": list(nextEntries.values())})
        except BaseException as error:
            self._pendingCleanup.update(created)
            self._cleanupScanRequired = True
            if not self._retirePending() and isinstance(error, Exception):
                raise OSError("capture failed; unpublished snapshot cleanup pending") from error
            raise
        self._entries = nextEntries
        self._pendingCleanup.update(obsolete)
        if not self._retirePending():
            raise OSError("snapshot committed; preceding capture cleanup pending")



class PreviewAssetStore:
    def __init__(self, root: Path) -> None:
        self.root = _ioPath(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.transientRoot = self.root / "_transient"
        self.transientRoot.mkdir(parents=True, exist_ok=True)
        self._assets: dict[str, PreviewAsset] = {}
        self._lock = threading.RLock()
        self._pendingCleanup: set[Path] = set()
        self._cleanupScanRequired = True
        self._loadPersistentAssets()

    def _retirePending(self) -> bool:
        if self._cleanupScanRequired:
            try:
                # Reconstruct ownership after reopening: only private files no
                # longer named by a committed project index may be discarded.
                for projectRoot in self.root.iterdir():
                    if not projectRoot.is_dir() or projectRoot.name.startswith("_"):
                        continue
                    referenced = {projectRoot / str(entry["relativePath"])
                                  for entry in _readIndex(projectRoot / "index.json") if _validEntry(entry)}
                    assetsRoot = projectRoot / "assets"
                    if assetsRoot.is_dir():
                        self._pendingCleanup.update(path for path in assetsRoot.iterdir()
                                                    if path.is_file() and path not in referenced)
                    self._pendingCleanup.update(projectRoot.glob(".index.json.*.tmp"))
            except OSError:
                return False
            self._cleanupScanRequired = False
        return _retirePaths(self._pendingCleanup)

    @staticmethod
    def projectKey(projectPath: str, projectId: str) -> str:
        try:
            normalizedPath = os.path.normcase(str(Path(projectPath).resolve(strict=False)))
        except (OSError, RuntimeError):
            normalizedPath = os.path.normcase(str(Path(projectPath).absolute()))
        return hashlib.sha256(f"{normalizedPath}\0{projectId}".encode("utf-8")).hexdigest()

    def promote(self, stagingRoot: Path, projectKey: str) -> None:
        with self._lock:
            self._promoteLocked(_ioPath(stagingRoot), projectKey)

    def _promoteLocked(self, stagingRoot: Path, projectKey: str) -> None:
        if not self._retirePending():
            raise OSError("preview asset cleanup pending; new promotion skipped")
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
        obsolete: list[Path] = []
        created: list[Path] = []
        try:
            for rawEntry in index:
                if not _validEntry(rawEntry):
                    continue
                entry = dict(rawEntry)
                source = stagingRoot / str(entry["relativePath"])
                if not source.exists() or not source.is_file():
                    continue
                suffix = source.suffix.lower()
                identity = _assetIdentity(projectKey, entry)
                relativePath = f"assets/{identity}{suffix}"
                destination = projectRoot / relativePath
                if not destination.exists():
                    created.append(destination)
                _atomicCopy(source, destination)
                old = merged.get(_entryKey(entry))
                if old is not None:
                    oldPath = projectRoot / str(old.get("relativePath", ""))
                    if oldPath != destination:
                        obsolete.append(oldPath)
                entry["relativePath"] = relativePath
                merged[_entryKey(entry)] = entry
            _atomicJson(projectRoot / "index.json", {"entries": list(merged.values())})
        except BaseException as error:
            # New capture identities are unpublished until the index commits.
            self._pendingCleanup.update(created)
            self._cleanupScanRequired = True
            if not self._retirePending() and isinstance(error, Exception):
                raise OSError("promotion failed; unpublished asset cleanup pending") from error
            raise
        try:
            self._enforceLimits()
        finally:
            # The committed index is authoritative even if quota maintenance
            # reports an error. Old IDs must not keep stale, retargetable maps.
            try:
                self._loadPersistentAssets()
            finally:
                self._pendingCleanup.update(obsolete)
                # Quota eviction may also have failed to remove an unindexed
                # file; account for it before admitting another promotion.
                self._cleanupScanRequired = True
                if not self._retirePending():
                    raise OSError("snapshot promoted; preceding asset cleanup pending")

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
            if asset is None or not asset.path.exists() or not asset.projectKey or not asset.captureId:
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
                        and other.captureId == asset.captureId
                        and other.originJobId == asset.originJobId
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
                and asset.path.is_file()
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
                    identity = _assetIdentity(projectRoot.name, entry)
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
                        originJobId=str(entry.get("originJobId", "")),
                        originProjectRevision=_intValue(entry.get("originProjectRevision", 0)),
                        captureId=str(entry.get("captureId", "")),
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


def _assetIdentity(projectKey: str, entry: Mapping[str, object]) -> str:
    identity = projectKey + "\0" + "\0".join(_entryKey(entry))
    captureId = entry.get("captureId")
    if isinstance(captureId, str) and captureId:
        identity += "\0" + captureId
    # No capture ID means a persisted pre-policy snapshot: retain its legacy ID
    # until replaced, but never present it as a verified current-run source.
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


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


def _retirePaths(pending: set[Path]) -> bool:
    for path in tuple(pending):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            continue
        pending.remove(path)
    return not pending


def _unlink(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
