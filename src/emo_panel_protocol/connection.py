"""Qt-free Runtime connection identity and channel construction."""
from dataclasses import dataclass
import math
from urllib.parse import urlsplit

import grpc


def validateRuntimeTarget(value: str) -> str:
    target = value.strip()
    try:
        address = urlsplit("//" + target)
        valid = bool(address.hostname and address.port and not address.path and not address.query
                     and not address.fragment and not address.username and not address.password
                     and not any(char.isspace() for char in target))
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("请输入 Runtime 的主机:端口，例如 127.0.0.1:50051。")
    return target


def formatTarget(host: str, port: int) -> str:
    return f"[{host}]:{port}" if ":" in host and not host.startswith("[") else f"{host}:{port}"


@dataclass(frozen=True)
class ConnectionProfile:
    target: str
    timeoutSeconds: float = 5.0
    maxReceiveMessageBytes: int = 9 * 1024 * 1024

    def __post_init__(self):
        object.__setattr__(self, "target", validateRuntimeTarget(self.target))
        if (isinstance(self.timeoutSeconds, bool) or not isinstance(self.timeoutSeconds, (int, float))
                or not math.isfinite(self.timeoutSeconds) or not 0 < self.timeoutSeconds <= 60):
            raise ValueError("Runtime request timeout must be between 0 and 60 seconds")
        if (type(self.maxReceiveMessageBytes) is not int
                or not 1024 <= self.maxReceiveMessageBytes <= 64 * 1024 * 1024):
            raise ValueError("Runtime receive budget must be between 1 KiB and 64 MiB")

    @property
    def channelOptions(self):
        return (("grpc.max_receive_message_length", self.maxReceiveMessageBytes),)


def connectionProfile(value: str | ConnectionProfile) -> ConnectionProfile:
    return value if isinstance(value, ConnectionProfile) else ConnectionProfile(value)


def createChannel(profile: str | ConnectionProfile, *, asynchronous=False, options=()):
    """Both control and viewing use the same explicit endpoint and receive budget."""
    resolved = connectionProfile(profile)
    settings = {**dict(resolved.channelOptions), **dict(options)}
    factory = grpc.aio.insecure_channel if asynchronous else grpc.insecure_channel
    return factory(resolved.target, options=tuple(settings.items()))
