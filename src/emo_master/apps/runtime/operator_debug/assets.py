"""Session-owned immutable assets; transport uses bounded chunks, never paths."""
from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
import re
import struct
from uuid import uuid4

import cv2
import numpy as np

from .contracts import MAX_INLINE_BYTES, encode, fail, parse

CHUNK_BYTES = 256 * 1024
VALUE_BYTES = 64 * 1024 * 1024
CACHE_BYTES = 256 * 1024 * 1024
ASSET_COUNT = 1024


def imageBytes(value):
    if (not isinstance(value, np.ndarray) or value.dtype != np.uint8
            or value.ndim not in {2, 3} or (value.ndim == 3 and value.shape[2] != 3)):
        fail("E_DEBUG_UNSUPPORTED", "only uint8 GRAY or BGR images are supported")
    if not value.size or value.nbytes > VALUE_BYTES:
        fail("E_DEBUG_LIMIT", "decoded image exceeds 64 MiB or is empty")
    ok, data = cv2.imencode(".png", value)
    if not ok:
        fail("E_INPUT_TYPE", "PNG encoding failed")
    return data.tobytes()


def decode(data, mime):
    if len(data) > VALUE_BYTES:
        fail("E_DEBUG_LIMIT", "asset exceeds 64 MiB")
    if mime == "application/json":
        try:
            return parse(data.decode("utf-8"), VALUE_BYTES), len(data)
        except UnicodeError as error:
            fail("E_INPUT_TYPE", str(error))
    if mime != "image/png":
        fail("E_DEBUG_UNSUPPORTED", "asset must be PNG or strict JSON")
    # Inspect dimensions and bit depth before asking the decoder to allocate.
    if len(data) < 33 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        fail("E_INPUT_TYPE", "invalid PNG header")
    width, height, depth, color = struct.unpack(">IIBB", data[16:26])
    if depth != 8 or color not in {0, 2}:
        fail("E_DEBUG_UNSUPPORTED", "PNG must be 8-bit grayscale or RGB without alpha")
    size = width * height * (3 if color == 2 else 1)
    if not width or not height or size > VALUE_BYTES:
        fail("E_DEBUG_LIMIT", "decoded PNG exceeds 64 MiB")
    value = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_UNCHANGED)
    if value is None or value.shape[:2] != (height, width) or value.nbytes != size:
        fail("E_INPUT_TYPE", "invalid PNG data")
    return value, size


class DebugAssets:
    def __init__(self, workspace, *, budget=CACHE_BYTES):
        self.root = Path(workspace) / "debug_assets"
        self.root.mkdir(exist_ok=True)
        self.budget = budget
        self.entries = {}
        self.pending = {}

    @property
    def used(self):
        return sum(row["charge"] for row in self.entries.values()) + sum(row["total"] for row in self.pending.values())

    def _path(self, assetId, suffix=".bin"):
        if not isinstance(assetId, str) or not re.fullmatch(r"[a-f0-9]{32}", assetId):
            fail("E_DEBUG_RESULT_EXPIRED", "unknown session asset")
        return self.root / (assetId + suffix)

    def describe(self, assetId):
        row = self.entries.get(assetId)
        if row is None:
            fail("E_DEBUG_RESULT_EXPIRED", "asset is expired or belongs to another session")
        return deepcopy(row)

    def upload(self, *, assetId, offset, total, data, mime, sha256, provenance):
        if not data or len(data) > CHUNK_BYTES or not 0 < total <= VALUE_BYTES:
            fail("E_DEBUG_LIMIT", "invalid asset or chunk size")
        if not re.fullmatch(r"[a-f0-9]{64}", sha256) or mime not in {"image/png", "application/json"}:
            fail("E_INPUT_TYPE", "invalid asset digest or MIME type")
        encode(provenance)
        if not isinstance(provenance, dict):
            fail("E_INPUT_TYPE", "provenance must be an object")
        if not assetId:
            if offset or self.used + total > self.budget or len(self.entries) + len(self.pending) >= ASSET_COUNT:
                fail("E_DEBUG_LIMIT", "asset quota exceeded or invalid initial offset")
            assetId = uuid4().hex
            self.pending[assetId] = dict(total=total, offset=0, mime=mime, sha256=sha256, provenance=deepcopy(provenance))
        row = self.pending.get(assetId)
        if row is None or any(row[key] != value for key, value in (
                ("total", total), ("offset", offset), ("mime", mime), ("sha256", sha256), ("provenance", provenance))):
            fail("E_DEBUG_REQUEST_CONFLICT", "upload identity, metadata or offset changed")
        if offset + len(data) > total:
            fail("E_DEBUG_LIMIT", "chunk exceeds declared asset size")
        path = self._path(assetId, ".part")
        try:
            with path.open("ab") as stream:
                if stream.write(data) != len(data):
                    raise OSError("incomplete asset write")
            row["offset"] += len(data)
            if row["offset"] < total:
                return dict(assetId=assetId, offset=row["offset"], complete=False)
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != sha256:
                fail("E_INPUT_TYPE", "asset digest mismatch")
            value, memory = decode(raw, mime)
            if self.used + memory > self.budget:
                fail("E_DEBUG_LIMIT", "decoded asset exceeds session budget")
            entry = self._entry(assetId, raw, mime, memory, provenance, value)
            path.replace(self._path(assetId))
            self.entries[assetId] = entry
            del self.pending[assetId]
            return dict(assetId=assetId, offset=total, complete=True, asset=self.describe(assetId))
        except Exception:
            self._remove(path)
            self.pending.pop(assetId, None)
            raise

    def _remove(self, path):
        try:
            path.unlink(missing_ok=True)
        except OSError as error:
            fail("E_RESOURCE_CLEANUP_FAILED", str(error))

    def _entry(self, assetId, raw, mime, memory, provenance, value):
        entry = dict(assetId=assetId, mime=mime, bytes=len(raw), charge=len(raw)+memory,
                     sha256=hashlib.sha256(raw).hexdigest(), provenance=deepcopy(provenance))
        if isinstance(value, np.ndarray):
            entry.update(shape=list(value.shape), dtype="uint8", width=value.shape[1], height=value.shape[0])
        return entry

    def put(self, value, provenance):
        if isinstance(value, np.ndarray):
            raw, mime, memory = imageBytes(value), "image/png", value.nbytes
        else:
            raw, mime = encode(value, VALUE_BYTES).encode(), "application/json"
            memory = len(raw)
        if len(raw) > VALUE_BYTES or self.used + len(raw) + memory > self.budget or len(self.entries) >= ASSET_COUNT:
            fail("E_DEBUG_LIMIT", "asset retention budget exceeded")
        assetId = uuid4().hex
        entry = self._entry(assetId, raw, mime, memory, provenance, value)
        self.entries[assetId] = entry
        try:
            self._path(assetId).write_bytes(raw)
        except OSError:
            self._remove(self._path(assetId))
            del self.entries[assetId]
            raise
        return self.describe(assetId)

    def discard(self):
        for assetId in list(self.entries):
            self._remove(self._path(assetId))
            del self.entries[assetId]

    def adopt(self, entry):
        assetId = entry["assetId"]
        if assetId in self.entries or self.used + entry["charge"] > self.budget:
            fail("E_DEBUG_LIMIT", "worker asset cannot be retained")
        raw = self._path(assetId).read_bytes()
        if len(raw) != entry["bytes"] or hashlib.sha256(raw).hexdigest() != entry["sha256"]:
            fail("E_INPUT_TYPE", "worker asset is incomplete")
        self.entries[assetId] = deepcopy(entry)

    def value(self, assetId):
        row = self.describe(assetId)
        value, _ = decode(self._path(assetId).read_bytes(), row["mime"])
        return value

    def read(self, assetId, offset):
        row = self.describe(assetId)
        if not 0 <= offset <= row["bytes"]:
            fail("E_INPUT_TYPE", "invalid asset read offset")
        with self._path(assetId).open("rb") as stream:
            stream.seek(offset)
            return stream.read(CHUNK_BYTES), row

    def collectOutputs(self, retained):
        for assetId, row in list(self.entries.items()):
            if row["provenance"].get("kind") == "debug-output" and assetId not in retained:
                self._remove(self._path(assetId))
                del self.entries[assetId]


def freezeOutputs(outputs, assets, provenance):
    inline, references = {}, {}
    for port, value in outputs.items():
        if not isinstance(value, np.ndarray):
            try:
                if len(encode(value, MAX_INLINE_BYTES)) <= 4096:
                    inline[port] = value
                    continue
            except ValueError:
                pass
        references[port] = assets.put(value, dict(provenance, port=port))
    return inline, references
