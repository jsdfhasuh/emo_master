from __future__ import annotations

import base64
import binascii
import math
import struct
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TypeAlias


COMMUNICATION_PAYLOAD_SCHEMA_VERSION = "1.0"
SUPPORTED_COMMUNICATION_PAYLOAD_SCHEMA_VERSIONS = frozenset({"1.0"})
COMMUNICATION_PAYLOAD_PORT_TYPES = frozenset(
    {"plcValueCollection", "plcWriteReceipt", "tcpMessage"}
)
PLC_PROTOCOL = "mitsubishiSlmp3e"
PLC_DATA_TYPES = frozenset(
    {"uint16", "int16", "uint32", "int32", "float32", "bit"}
)
TCP_MESSAGE_ENCODINGS = frozenset({"utf-8", "ascii", "latin-1", "hex", "base64"})

PlcScalar: TypeAlias = int | float | bool


class CommunicationPayloadValidationError(ValueError):
    """Raised when a value violates a communication payload contract."""


def _object(value: object, path: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise CommunicationPayloadValidationError(f"{path} must be an object")
    if any(not isinstance(key, str) for key in value):
        raise CommunicationPayloadValidationError(f"{path} keys must be strings")
    return value


def _keys(
    payload: Mapping[str, object],
    path: str,
    required: set[str],
    optional: set[str] | None = None,
) -> None:
    optional = optional or set()
    actual = set(payload)
    missing = sorted(required - actual)
    if missing:
        raise CommunicationPayloadValidationError(
            f"{path} is missing: {', '.join(missing)}"
        )
    unknown = sorted(actual - required - optional)
    if unknown:
        raise CommunicationPayloadValidationError(
            f"{path} has unknown fields: {', '.join(unknown)}"
        )


def _typedObject(
    value: object,
    path: str,
    payloadType: str,
    required: set[str],
    optional: set[str] | None = None,
) -> Mapping[str, object]:
    payload = _object(value, path)
    _keys(payload, path, {"type", "schemaVersion", *required}, optional)
    if payload["type"] != payloadType:
        raise CommunicationPayloadValidationError(
            f"{path}.type must be {payloadType!r}, got {payload['type']!r}"
        )
    validateCommunicationPayloadSchemaVersion(payload["schemaVersion"], path)
    return payload


def validateCommunicationPayloadSchemaVersion(
    value: object,
    path: str = "payload",
) -> str:
    if not isinstance(value, str):
        raise CommunicationPayloadValidationError(
            f"{path}.schemaVersion must be a string"
        )
    parts = value.split(".")
    if len(parts) != 2 or any(
        not part.isdigit() or (len(part) > 1 and part.startswith("0"))
        for part in parts
    ):
        raise CommunicationPayloadValidationError(
            f"{path}.schemaVersion must use major.minor numeric form"
        )
    if value not in SUPPORTED_COMMUNICATION_PAYLOAD_SCHEMA_VERSIONS:
        raise CommunicationPayloadValidationError(
            f"unsupported {path}.schemaVersion: {value!r}; supported: "
            f"{', '.join(sorted(SUPPORTED_COMMUNICATION_PAYLOAD_SCHEMA_VERSIONS))}"
        )
    return value


def isCommunicationPayloadSchemaVersionCompatible(
    actualVersion: object,
    expectedVersion: object | None = None,
) -> bool:
    try:
        actual = validateCommunicationPayloadSchemaVersion(actualVersion)
    except CommunicationPayloadValidationError:
        return False
    if expectedVersion is None:
        return True
    if not isinstance(expectedVersion, str):
        return False
    if expectedVersion.endswith(".x"):
        major = expectedVersion[:-2]
        return major.isdigit() and actual.split(".", 1)[0] == major
    return actual == expectedVersion


def _string(value: object, path: str, *, allowEmpty: bool = False) -> str:
    if not isinstance(value, str) or (not allowEmpty and value == ""):
        requirement = "a string" if allowEmpty else "a non-empty string"
        raise CommunicationPayloadValidationError(f"{path} must be {requirement}")
    return value


def _integer(
    value: object,
    path: str,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise CommunicationPayloadValidationError(f"{path} must be an integer")
    if minimum is not None and value < minimum:
        raise CommunicationPayloadValidationError(f"{path} must be >= {minimum}")
    if maximum is not None and value > maximum:
        raise CommunicationPayloadValidationError(f"{path} must be <= {maximum}")
    return value


def _finiteNumber(value: object, path: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise CommunicationPayloadValidationError(f"{path} must be a number")
    result = float(value)
    if not math.isfinite(result):
        raise CommunicationPayloadValidationError(f"{path} must be finite")
    return result


def _plcValue(value: object, dataType: str, path: str) -> PlcScalar:
    if dataType == "bit":
        if not isinstance(value, bool):
            raise CommunicationPayloadValidationError(f"{path} must be boolean")
        return value
    if dataType == "float32":
        result = _finiteNumber(value, path)
        try:
            packed = struct.pack("<f", result)
            normalized = struct.unpack("<f", packed)[0]
        except (OverflowError, struct.error) as exc:
            raise CommunicationPayloadValidationError(
                f"{path} must be representable as IEEE-754 float32"
            ) from exc
        if not math.isfinite(normalized):
            raise CommunicationPayloadValidationError(
                f"{path} must be representable as IEEE-754 float32"
            )
        return result
    ranges = {
        "uint16": (0, 0xFFFF),
        "int16": (-0x8000, 0x7FFF),
        "uint32": (0, 0xFFFFFFFF),
        "int32": (-0x80000000, 0x7FFFFFFF),
    }
    minimum, maximum = ranges[dataType]
    return _integer(value, path, minimum=minimum, maximum=maximum)


def plcWordsPerValue(dataType: str) -> int:
    if dataType not in PLC_DATA_TYPES:
        raise CommunicationPayloadValidationError(
            f"unsupported PLC data type: {dataType!r}"
        )
    return 2 if dataType in {"uint32", "int32", "float32"} else 1


def plcDeviceAddressSpan(device: str, wordCount: int) -> int:
    """Return the number of device addresses covered by a word-unit request."""
    normalizedDevice = device.strip().upper() if isinstance(device, str) else ""
    if normalizedDevice not in {"D", "M"}:
        raise CommunicationPayloadValidationError("device must be 'D' or 'M'")
    normalizedWordCount = _integer(wordCount, "wordCount", minimum=1)
    return normalizedWordCount * 16 if normalizedDevice == "M" else normalizedWordCount


@dataclass(frozen=True)
class PlcValueCollection:
    device: str
    startAddress: int
    dataType: str
    values: tuple[PlcScalar, ...]
    wordCount: int

    def __post_init__(self) -> None:
        device = self.device.strip().upper() if isinstance(self.device, str) else ""
        if device not in {"D", "M"}:
            raise CommunicationPayloadValidationError("device must be 'D' or 'M'")
        object.__setattr__(self, "device", device)
        _integer(self.startAddress, "startAddress", minimum=0, maximum=0xFFFFFF)
        dataType = _string(self.dataType, "dataType")
        if dataType not in PLC_DATA_TYPES:
            raise CommunicationPayloadValidationError(
                f"dataType must be one of: {', '.join(sorted(PLC_DATA_TYPES))}"
            )
        if dataType == "bit" and device != "M":
            raise CommunicationPayloadValidationError("bit data requires device M")
        if dataType in {"uint32", "int32", "float32"} and device != "D":
            raise CommunicationPayloadValidationError(
                f"{dataType} data requires device D"
            )
        if not isinstance(self.values, tuple) or not self.values:
            raise CommunicationPayloadValidationError("values must be a non-empty tuple")
        normalized = tuple(
            _plcValue(value, dataType, f"values[{index}]")
            for index, value in enumerate(self.values)
        )
        object.__setattr__(self, "values", normalized)
        expectedWords = len(normalized) * plcWordsPerValue(dataType)
        _integer(self.wordCount, "wordCount", minimum=1, maximum=960)
        if self.wordCount != expectedWords:
            raise CommunicationPayloadValidationError(
                f"wordCount must be {expectedWords} for {len(normalized)} {dataType} values"
            )
        if (
            self.startAddress + plcDeviceAddressSpan(device, self.wordCount) - 1
            > 0xFFFFFF
        ):
            raise CommunicationPayloadValidationError(
                "PLC address range exceeds the 24-bit device space"
            )

    def toPayload(self) -> dict[str, object]:
        return {
            "type": "plcValueCollection",
            "schemaVersion": COMMUNICATION_PAYLOAD_SCHEMA_VERSION,
            "protocol": PLC_PROTOCOL,
            "device": self.device,
            "startAddress": self.startAddress,
            "dataType": self.dataType,
            "values": list(self.values),
            "wordCount": self.wordCount,
        }

    @classmethod
    def fromPayload(cls, value: object) -> PlcValueCollection:
        payload = _typedObject(
            value,
            "plcValueCollection",
            "plcValueCollection",
            {"protocol", "device", "startAddress", "dataType", "values", "wordCount"},
        )
        if payload["protocol"] != PLC_PROTOCOL:
            raise CommunicationPayloadValidationError(
                f"plcValueCollection.protocol must be {PLC_PROTOCOL!r}"
            )
        rawValues = payload["values"]
        if not isinstance(rawValues, list):
            raise CommunicationPayloadValidationError(
                "plcValueCollection.values must be an array"
            )
        return cls(
            device=_string(payload["device"], "plcValueCollection.device"),
            startAddress=_integer(
                payload["startAddress"],
                "plcValueCollection.startAddress",
                minimum=0,
                maximum=0xFFFFFF,
            ),
            dataType=_string(payload["dataType"], "plcValueCollection.dataType"),
            values=tuple(rawValues),
            wordCount=_integer(
                payload["wordCount"],
                "plcValueCollection.wordCount",
                minimum=1,
                maximum=960,
            ),
        )


@dataclass(frozen=True)
class PlcWriteReceipt:
    device: str
    startAddress: int
    dataType: str
    valueCount: int
    wordCount: int
    attempts: int

    def __post_init__(self) -> None:
        device = self.device.strip().upper() if isinstance(self.device, str) else ""
        if device not in {"D", "M"}:
            raise CommunicationPayloadValidationError("device must be 'D' or 'M'")
        object.__setattr__(self, "device", device)
        _integer(self.startAddress, "startAddress", minimum=0, maximum=0xFFFFFF)
        dataType = _string(self.dataType, "dataType")
        if dataType not in PLC_DATA_TYPES - {"bit"}:
            raise CommunicationPayloadValidationError(
                "PLC write dataType must be uint16, int16, uint32, int32, or float32"
            )
        if dataType in {"uint32", "int32", "float32"} and device != "D":
            raise CommunicationPayloadValidationError(
                f"{dataType} data requires device D"
            )
        _integer(self.valueCount, "valueCount", minimum=1)
        _integer(self.wordCount, "wordCount", minimum=1, maximum=960)
        if self.wordCount != self.valueCount * plcWordsPerValue(dataType):
            raise CommunicationPayloadValidationError(
                "wordCount does not match valueCount and dataType"
            )
        if (
            self.startAddress + plcDeviceAddressSpan(device, self.wordCount) - 1
            > 0xFFFFFF
        ):
            raise CommunicationPayloadValidationError(
                "PLC address range exceeds the 24-bit device space"
            )
        _integer(self.attempts, "attempts", minimum=1)

    def toPayload(self) -> dict[str, object]:
        return {
            "type": "plcWriteReceipt",
            "schemaVersion": COMMUNICATION_PAYLOAD_SCHEMA_VERSION,
            "protocol": PLC_PROTOCOL,
            "device": self.device,
            "startAddress": self.startAddress,
            "dataType": self.dataType,
            "valueCount": self.valueCount,
            "wordCount": self.wordCount,
            "attempts": self.attempts,
        }

    @classmethod
    def fromPayload(cls, value: object) -> PlcWriteReceipt:
        payload = _typedObject(
            value,
            "plcWriteReceipt",
            "plcWriteReceipt",
            {
                "protocol",
                "device",
                "startAddress",
                "dataType",
                "valueCount",
                "wordCount",
                "attempts",
            },
        )
        if payload["protocol"] != PLC_PROTOCOL:
            raise CommunicationPayloadValidationError(
                f"plcWriteReceipt.protocol must be {PLC_PROTOCOL!r}"
            )
        return cls(
            device=_string(payload["device"], "plcWriteReceipt.device"),
            startAddress=_integer(
                payload["startAddress"],
                "plcWriteReceipt.startAddress",
                minimum=0,
                maximum=0xFFFFFF,
            ),
            dataType=_string(payload["dataType"], "plcWriteReceipt.dataType"),
            valueCount=_integer(
                payload["valueCount"], "plcWriteReceipt.valueCount", minimum=1
            ),
            wordCount=_integer(
                payload["wordCount"],
                "plcWriteReceipt.wordCount",
                minimum=1,
                maximum=960,
            ),
            attempts=_integer(
                payload["attempts"], "plcWriteReceipt.attempts", minimum=1
            ),
        )


@dataclass(frozen=True)
class TcpMessage:
    data: str
    encoding: str
    byteLength: int
    peerHost: str | None = None
    peerPort: int | None = None

    def __post_init__(self) -> None:
        _string(self.data, "data", allowEmpty=True)
        encoding = _string(self.encoding, "encoding").lower()
        if encoding not in TCP_MESSAGE_ENCODINGS:
            raise CommunicationPayloadValidationError(
                f"encoding must be one of: {', '.join(sorted(TCP_MESSAGE_ENCODINGS))}"
            )
        object.__setattr__(self, "encoding", encoding)
        _integer(self.byteLength, "byteLength", minimum=0)
        if (self.peerHost is None) != (self.peerPort is None):
            raise CommunicationPayloadValidationError(
                "peerHost and peerPort must be provided together"
            )
        if self.peerHost is not None:
            _string(self.peerHost, "peerHost")
            _integer(self.peerPort, "peerPort", minimum=1, maximum=65535)
        actualLength = len(self.toBytes())
        if self.byteLength != actualLength:
            raise CommunicationPayloadValidationError(
                f"byteLength must be {actualLength}, got {self.byteLength}"
            )

    def toBytes(self) -> bytes:
        try:
            if self.encoding == "hex":
                return bytes.fromhex(self.data)
            if self.encoding == "base64":
                return base64.b64decode(self.data, validate=True)
            return self.data.encode(self.encoding)
        except (UnicodeEncodeError, ValueError, binascii.Error) as exc:
            raise CommunicationPayloadValidationError(
                f"data is not valid {self.encoding}: {exc}"
            ) from exc

    def toPayload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "type": "tcpMessage",
            "schemaVersion": COMMUNICATION_PAYLOAD_SCHEMA_VERSION,
            "data": self.data,
            "encoding": self.encoding,
            "byteLength": self.byteLength,
        }
        if self.peerHost is not None and self.peerPort is not None:
            payload["peerHost"] = self.peerHost
            payload["peerPort"] = self.peerPort
        return payload

    @classmethod
    def fromBytes(
        cls,
        value: bytes,
        *,
        encoding: str = "utf-8",
        peerHost: str | None = None,
        peerPort: int | None = None,
    ) -> TcpMessage:
        if not isinstance(value, bytes):
            raise CommunicationPayloadValidationError("value must be bytes")
        if not isinstance(encoding, str):
            raise CommunicationPayloadValidationError("encoding must be a string")
        normalizedEncoding = encoding.lower()
        try:
            if normalizedEncoding == "hex":
                data = value.hex()
            elif normalizedEncoding == "base64":
                data = base64.b64encode(value).decode("ascii")
            elif normalizedEncoding in {"utf-8", "ascii", "latin-1"}:
                data = value.decode(normalizedEncoding)
            else:
                raise CommunicationPayloadValidationError(
                    f"unsupported TCP message encoding: {encoding!r}"
                )
        except UnicodeDecodeError as exc:
            raise CommunicationPayloadValidationError(
                f"response is not valid {normalizedEncoding}: {exc}"
            ) from exc
        return cls(
            data=data,
            encoding=normalizedEncoding,
            byteLength=len(value),
            peerHost=peerHost,
            peerPort=peerPort,
        )

    @classmethod
    def fromPayload(cls, value: object) -> TcpMessage:
        payload = _typedObject(
            value,
            "tcpMessage",
            "tcpMessage",
            {"data", "encoding", "byteLength"},
            {"peerHost", "peerPort"},
        )
        peerHostValue = payload.get("peerHost")
        peerPortValue = payload.get("peerPort")
        return cls(
            data=_string(payload["data"], "tcpMessage.data", allowEmpty=True),
            encoding=_string(payload["encoding"], "tcpMessage.encoding"),
            byteLength=_integer(
                payload["byteLength"], "tcpMessage.byteLength", minimum=0
            ),
            peerHost=(
                _string(peerHostValue, "tcpMessage.peerHost")
                if peerHostValue is not None
                else None
            ),
            peerPort=(
                _integer(
                    peerPortValue,
                    "tcpMessage.peerPort",
                    minimum=1,
                    maximum=65535,
                )
                if peerPortValue is not None
                else None
            ),
        )


CommunicationPayload: TypeAlias = PlcValueCollection | PlcWriteReceipt | TcpMessage


def parseCommunicationPayload(value: object) -> CommunicationPayload:
    payload = _object(value, "communicationPayload")
    payloadType = payload.get("type")
    if payloadType == "plcValueCollection":
        return PlcValueCollection.fromPayload(payload)
    if payloadType == "plcWriteReceipt":
        return PlcWriteReceipt.fromPayload(payload)
    if payloadType == "tcpMessage":
        return TcpMessage.fromPayload(payload)
    raise CommunicationPayloadValidationError(
        f"unsupported communication payload type: {payloadType!r}"
    )


def matchesCommunicationPortType(
    value: object,
    expectedType: str,
    expectedSchemaVersion: object | None = None,
) -> bool:
    if not isinstance(value, dict):
        return False
    try:
        if not isCommunicationPayloadSchemaVersionCompatible(
            value.get("schemaVersion"), expectedSchemaVersion
        ):
            return False
        payload = parseCommunicationPayload(value)
        return value.get("type") == expectedType and payload is not None
    except (CommunicationPayloadValidationError, TypeError, ValueError):
        return False
