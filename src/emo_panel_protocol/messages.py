"""History JSON and browser notifications exchanged by Panel Contract v1.

Runtime requests and binary replies remain protobuf messages. These models cover
the JSON inside HistoryReply and the gateway's browser-facing notifications.
"""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, RootModel, model_validator

from .models import Id, Model
from .results import ClosedResult


Revision = Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]
Scalar = str | int | float | bool | None


class HistoryColumn(Model):
    name: str
    type: Literal['scalar'] = 'scalar'


class HistoryImageReference(Model):
    status: Literal['AVAILABLE', 'EMPTY', 'INVALID_VALUE']
    imageRef: str | None = None
    message: str | None = None

    @model_validator(mode='after')
    def checkReference(self):
        if (self.status == 'AVAILABLE') != bool(self.imageRef):
            raise ValueError('AVAILABLE history image requires an opaque reference')
        return self


class HistoryRow(Model):
    rowId: str
    values: list[Scalar]
    imageRefs: dict[Id, HistoryImageReference] = Field(default_factory=dict)


class HistoryPage(Model):
    columns: list[HistoryColumn]
    rows: list[HistoryRow]
    page: int = Field(ge=0)
    pageSize: int = Field(ge=1, le=100)
    hasNext: bool
    historyDefinitionRevision: Revision
    stableRowIdentity: bool

    @model_validator(mode='after')
    def checkRows(self):
        if any(len(row.values) != len(self.columns) for row in self.rows):
            raise ValueError('history row width must match columns')
        if self.stableRowIdentity and any(not row.rowId for row in self.rows):
            raise ValueError('stable row identity requires nonempty row IDs')
        return self


class HistoryAggregate(Model):
    value: Scalar
    aggregate: Literal['count', 'sum', 'avg', 'min', 'max']
    historyDefinitionRevision: Revision


class BrowserContext(Model):
    runtimeInstanceId: Id
    projectId: Id


class BrowserResult(BrowserContext):
    event: Literal['result'] = 'result'
    # Protobuf uint64 JSON is a decimal string; preserve it in the browser.
    cursor: Annotated[str, Field(pattern=r'^[0-9]+$')]
    result: ClosedResult


class BrowserCaptureUnavailable(BrowserContext):
    event: Literal['capture_unavailable'] = 'capture_unavailable'
    jobId: Id
    reason: str


class BrowserJobChanged(BrowserContext):
    event: Literal['job_changed'] = 'job_changed'
    previousJobId: Id | None = None
    jobId: Id
    runGroupId: Id


class BrowserJobState(BrowserContext):
    event: Literal['job_state'] = 'job_state'
    jobId: Id
    runGroupId: Id
    status: str
    active: bool
    ownsResources: bool
    displayResourcesReleased: bool
    allowedActions: list[str]


class BrowserControlResult(BrowserContext):
    event: Literal['control_result'] = 'control_result'
    operation: Literal['start', 'stop']
    jobId: Id | None = None
    runGroupId: Id | None = None
    startRequestId: Id | None = None
    outcome: Literal['ACCEPTED', 'REJECTED', 'UNKNOWN']
    message: str


class BrowserError(BrowserContext):
    event: Literal['error'] = 'error'
    jobId: Id | None = None
    code: str
    message: str


class BrowserMessage(RootModel[Annotated[
        BrowserResult | BrowserCaptureUnavailable | BrowserJobChanged | BrowserJobState |
        BrowserControlResult | BrowserError, Field(discriminator='event')]]):
    """Gateway notification contract; browser actions use fixed HTTP endpoints."""
