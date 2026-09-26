"""Closed-result/asset DTOs only; no exporter, cache or subscription implementation."""
from __future__ import annotations

from typing import Literal

from pydantic import ConfigDict, Field, model_validator

from emo_master.core.presentation.models import Id, Model


class FrozenModel(Model):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, allow_inf_nan=False)


class ResultIdentity(FrozenModel):
    runtimeInstanceId: Id
    jobId: Id
    resultScopeId: Id
    invocationId: Id
    resultKey: Id
    resultOrdinal: int = Field(ge=1)
    executionRevision: str = Field(pattern=r"^[0-9a-f]{64}$")
    capturePlanRevision: str = Field(pattern=r"^[0-9a-f]{64}$")
    mode: Literal["debug", "release"]


class FrameProvenance(FrozenModel):
    frameIdentity: Id
    coordinateSpaceId: Id
    trust: Literal["proven", "unknown"]
    parentFrameIdentity: Id | None = None
    adapterVersion: Id | None = None


class ImageRef(FrozenModel):
    resourceId: Id
    ownerResultKey: Id
    byteSize: int = Field(gt=0, le=8 * 1024 * 1024)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    mimeType: Literal["image/png", "image/jpeg"]
    provenance: FrameProvenance
    ownership: Literal["result_store"] = "result_store"


class AssetLease(FrozenModel):
    leaseId: Id
    resourceId: Id
    ttlMs: int = Field(gt=0, le=30000)
    # Lease expiry uses the owner's monotonic clock; never compare clocks across hosts.
    clock: Literal["owner_monotonic"] = "owner_monotonic"


class ClosedSource(FrozenModel):
    sourceId: Id
    state: Literal["AVAILABLE", "UNAVAILABLE"]
    valueJson: str | None = Field(default=None, max_length=256 * 1024)
    image: ImageRef | None = None
    reason: str | None = None

    @model_validator(mode="after")
    def checkAvailability(self) -> ClosedSource:
        if self.state == "AVAILABLE":
            if (self.valueJson is None) == (self.image is None) or self.reason is not None:
                raise ValueError("available source requires exactly one frozen value or owned asset")
            if self.valueJson is not None:
                import json
                if len(self.valueJson.encode("utf-8")) > 256 * 1024:
                    raise ValueError("source exceeds byte budget")
                def rejectConstant(value):
                    raise ValueError(f"non-finite JSON constant: {value}")
                json.loads(self.valueJson, parse_constant=rejectConstant)
        elif not self.reason or self.valueJson is not None or self.image is not None:
            raise ValueError("unavailable source requires reason and no stale payload")
        return self


class ClosedResult(FrozenModel):
    identity: ResultIdentity
    expectedSourceIds: tuple[Id, ...] = Field(max_length=16)
    sources: tuple[ClosedSource, ...] = Field(max_length=16)
    status: Literal["COMPLETE", "INCOMPLETE", "FAILED", "CANCELLED"]
    executionTerminal: Literal["COMPLETED", "FAILED", "CANCELLED"]

    @model_validator(mode="after")
    def checkManifest(self) -> ClosedResult:
        actual = [source.sourceId for source in self.sources]
        if (len(set(actual)) != len(actual) or len(set(self.expectedSourceIds)) != len(self.expectedSourceIds)
                or set(actual) != set(self.expectedSourceIds)):
            raise ValueError("closed result must account for every expected source exactly once")
        complete = all(source.state == "AVAILABLE" for source in self.sources)
        expected = ("COMPLETE" if complete else "INCOMPLETE") if self.executionTerminal == "COMPLETED" else self.executionTerminal
        if self.status != expected:
            raise ValueError("status contradicts execution terminal or export outcome")
        if sum(len(source.valueJson.encode()) for source in self.sources if source.valueJson) > 1024 * 1024:
            raise ValueError("result value byte budget exceeded")
        for source in self.sources:
            if source.image and source.image.ownerResultKey != self.identity.resultKey:
                raise ValueError("asset must be owned by this result before closure")
        if sum(source.image.byteSize for source in self.sources if source.image) > 16 * 1024 * 1024:
            raise ValueError("result image byte budget exceeded")
        return self


class ResultNotification(FrozenModel):
    messageCursor: int = Field(ge=1)
    result: ClosedResult
