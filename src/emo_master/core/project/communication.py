"""Project-owned configuration. Constructing or validating it never performs I/O."""
from __future__ import annotations

from ipaddress import IPv4Address
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator


class CommunicationModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class TcpChannelSettings(CommunicationModel):
    channelId: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(min_length=1, max_length=80)
    allowedIps: list[str] = Field(min_length=1, max_length=16)

    @field_validator("allowedIps")
    @classmethod
    def validateIps(cls, values):
        result = [str(IPv4Address(value)) for value in values]
        if len(result) != len(set(result)):
            raise ValueError("同通道的客户端 IP 不能重复")
        if any(IPv4Address(value).is_unspecified or IPv4Address(value).is_multicast for value in result):
            raise ValueError("客户端必须是明确的 IPv4 地址")
        return result


class TcpServerSettings(CommunicationModel):
    serviceId: str = Field(default="tcp-server", min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(default="TCP 通信", min_length=1, max_length=80)
    bindAddress: str = "127.0.0.1"
    port: int = Field(default=10000, ge=1, le=65535)
    channels: list[TcpChannelSettings] = Field(default_factory=list, max_length=64)
    sendTimeoutMs: int = Field(default=2000, ge=1, le=60000)
    maxMessageBytes: int = Field(default=1048576, ge=1, le=16777216)

    @field_validator("bindAddress")
    @classmethod
    def validateBind(cls, value):
        address = IPv4Address(value)
        if address.is_multicast:
            raise ValueError("监听地址不能为组播地址")
        return str(address)

    @model_validator(mode="after")
    def validateRoutes(self):
        ids = [channel.channelId for channel in self.channels]
        ips = [ip for channel in self.channels for ip in channel.allowedIps]
        if len(ids) != len(set(ids)) or len(ips) != len(set(ips)):
            raise ValueError("通道 ID 与跨通道客户端 IP 必须唯一")
        return self


class CommunicationSettings(CommunicationModel):
    enabled: bool = False
    startupPolicy: Literal["on_project_run", "manual"] = "on_project_run"
    tcpServer: TcpServerSettings = Field(default_factory=TcpServerSettings)

    @model_validator(mode="after")
    def validateEnabled(self):
        if self.enabled and not self.tcpServer.channels:
            raise ValueError("启用 TCP 通信前请配置至少一个客户端通道")
        return self

    def transportPayload(self):
        payload = self.tcpServer.model_dump(mode="json")
        payload.pop('name')
        for channel in payload['channels']:
            channel.pop('name')
        return payload


def projectCommunication(document) -> CommunicationSettings:
    return document.communication or CommunicationSettings()


def tcpServerConfiguration(definition):
    """Pure adapter; lifecycle policy belongs exclusively to DeviceManager."""
    return CommunicationSettings(enabled=definition.enabled, startupPolicy="manual", tcpServer=TcpServerSettings(
        serviceId="tcp-server", name=definition.name[:80], **definition.connection,
        sendTimeoutMs=definition.policies.get("sendTimeoutMs", 2000),
        maxMessageBytes=definition.policies.get("maxMessageBytes", 1048576)))


def validateTcpDefinition(definition):
    payload = definition.model_dump()
    if definition.deviceTypeId == "device.tcp.server":
        try:
            config = tcpServerConfiguration(definition)
        except ValidationError as error:
            from emo_master.core.workflow.errors import ValidationIssue, WorkflowCompileError
            raise WorkflowCompileError([ValidationIssue('E_DEVICE_DEFINITION', item['msg'],
                fieldPath='connection.'+'.'.join(str(part) for part in item['loc'] if part != 'tcpServer')
                    if item['loc'] else 'connection.channels') for item in error.errors()]) from error
        payload["connection"] = {key: getattr(config.tcpServer, key) for key in ("bindAddress", "port")}
        payload["connection"]["channels"] = [item.model_dump() for item in config.tcpServer.channels]
    elif definition.deviceTypeId == "device.tcp.receive_once":
        host = IPv4Address(definition.connection["bindHost"])
        if host.is_multicast:
            from emo_master.core.workflow.errors import ValidationIssue, WorkflowCompileError
            raise WorkflowCompileError([ValidationIssue('E_DEVICE_DEFINITION', 'connection.bindHost cannot be multicast',
                                                       fieldPath='connection.bindHost')])
        payload["connection"]["bindHost"] = str(host)
    from emo_master.core.project.devices import DeviceDefinition
    return DeviceDefinition.model_validate(payload)
