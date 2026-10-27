"""Serializable managed-TCP values. A session is a revocable capability, not a socket."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TcpValue(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schemaVersion: Literal["1.1"] = "1.1"

    def toPayload(self):
        return self.model_dump(mode="json")

    @classmethod
    def fromPayload(cls, value):
        return cls.model_validate(value)


class TcpSessionRef(TcpValue):
    deviceId: str = Field(min_length=1, max_length=160)
    type: Literal["tcpSessionRef"] = "tcpSessionRef"
    runtimeInstanceId: str = Field(min_length=1, max_length=160)
    projectId: str = Field(min_length=1, max_length=160)
    projectLoadGeneration: str = Field(min_length=1, max_length=160)
    serviceId: str = Field(min_length=1, max_length=80)
    serviceGeneration: int = Field(ge=1)
    configRevision: str = Field(min_length=1, max_length=160)
    channelId: str = Field(min_length=1, max_length=80)
    connectionGeneration: int = Field(ge=1)
    leaseId: str = Field(min_length=16, max_length=160)
    jobId: str = Field(min_length=1, max_length=160)


class TcpSendReceipt(TcpValue):
    deviceId: str = Field(min_length=1, max_length=160)
    type: Literal["tcpSendReceipt"] = "tcpSendReceipt"
    requestId: str = Field(min_length=1, max_length=200)
    jobId: str = Field(min_length=1, max_length=160)
    channelId: str = Field(min_length=1, max_length=80)
    serviceGeneration: int = Field(ge=1)
    connectionGeneration: int = Field(ge=1)
    outcome: Literal["NOT_SENT", "SENT_TO_TRANSPORT", "OUTCOME_UNKNOWN"]
    bytesRequested: int = Field(ge=0, le=16777216)
    bytesSent: int = Field(ge=0, le=16777216)
    deviceConfirmed: Literal[False] = False
    code: str = ""
    message: str = ""

    @model_validator(mode="after")
    def validateCounts(self):
        if self.bytesSent > self.bytesRequested:
            raise ValueError("sent bytes exceed requested bytes")
        if self.outcome == "NOT_SENT" and self.bytesSent:
            raise ValueError("NOT_SENT cannot contain sent bytes")
        if self.outcome == "SENT_TO_TRANSPORT" and self.bytesSent != self.bytesRequested:
            raise ValueError("transport completion requires all bytes")
        return self


class TcpServiceError(RuntimeError):
    def __init__(self, code, message, receipt=None):
        self.code = code
        self.receipt = receipt
        self.diagnostics = {"tcpReceipt": receipt} if receipt else {}
        super().__init__(message)


def tcpActionBudget(deviceTypeId, action, params, policies):
    """Shared Worker/Runtime budgets, in milliseconds; no cached peer budget."""
    queue = policies.get('deviceWaitTimeoutMs', 5000)
    if deviceTypeId == 'device.tcp.client_once':
        budget = params['connectTimeoutMs'] + params['responseTimeoutMs'] * (2 if action == 'exchange' else 1)
    elif deviceTypeId == 'device.tcp.receive_once':
        budget = params['acceptTimeoutMs'] + params['readTimeoutMs'] * (2 if params.get('ackText') else 1)
    elif action == 'acquire_session':
        budget = params['timeoutMs']
    else:
        budget = policies['sendTimeoutMs']
    return dict(queueWaitMs=queue, actionBudgetMs=budget)
