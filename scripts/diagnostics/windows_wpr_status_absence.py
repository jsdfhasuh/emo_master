"""Offline-only, fail-closed WPR scoped-status body classifier prototype.

This module executes nothing, reads no files, and proves no live session state.
Capture facts are abstract inputs; a future caller must establish them from the
actual bounded private capture, EOF, and process-completion evidence.
"""

from dataclasses import dataclass, field
from enum import Enum


MAX_CAPTURE_BYTES = 4096
ALLOWED_EXIT_CODES = frozenset((0, 0xC5583000))
ABSENCE_BODY = "WPR is not recording"
ASCII_OUTER_WHITESPACE = " \t\n\r\v\f"


class Status(str, Enum):
    ABSENT = "ABSENT"
    UNKNOWN = "UNKNOWN"


class Reason(str, Enum):
    EXACT_ABSENCE_BODY = "exact_absence_body"
    INVALID_INPUT = "invalid_input"
    OUTPUT_NOT_PRIVATE = "output_not_private"
    COMMAND_NOT_NORMAL = "command_not_normal"
    OUTPUT_INCOMPLETE = "output_incomplete"
    OUTPUT_OVERSIZE = "output_oversize"
    EXIT_NOT_ALLOWED = "exit_not_allowed"
    ENCODING_INVALID = "encoding_invalid"
    REPEATED_BOM = "repeated_bom"
    BODY_NOT_EXACT = "body_not_exact"


@dataclass(frozen=True, slots=True)
class StatusObservation:
    # Input repr never exposes captured text, paths, IDs, or malformed metadata.
    body: bytes = field(repr=False)
    output_complete: bool = field(repr=False)
    normal_completion: bool = field(repr=False)
    private_capture: bool = field(repr=False)
    exit_code: int | None = field(repr=False)


@dataclass(frozen=True, slots=True)
class StatusDecision:
    status: Status
    reason: Reason

    def public_record(self) -> dict[str, str]:
        """Return only closed-set diagnostics, never any observation contents."""
        return {"status": self.status.value, "reason": self.reason.value}


_UTF8_BOM = b"\xef\xbb\xbf"
_UTF16LE_BOM = b"\xff\xfe"
_UTF16BE_BOM = b"\xfe\xff"
_UTF32LE_BOM = b"\xff\xfe\x00\x00"
_UTF32BE_BOM = b"\x00\x00\xfe\xff"
_SUPPORTED_BOMS = (
    (_UTF8_BOM, "utf-8"),
    (_UTF16LE_BOM, "utf-16-le"),
    (_UTF16BE_BOM, "utf-16-be"),
)


def _unknown(reason: Reason) -> StatusDecision:
    return StatusDecision(Status.UNKNOWN, reason)


def classify_status_absence(observation: StatusObservation) -> StatusDecision:
    """Return ABSENT only when every abstract capture gate and exact body pass.

    Gate order, and thus reason precedence, is: input types, private capture,
    normal command completion, complete capture, byte bound, exit allowlist,
    encoding/BOM validation, then exact whole-body equality. A complete flag is
    not an EOF observer, and this function must not be used as one.
    """
    if type(observation) is not StatusObservation:
        return _unknown(Reason.INVALID_INPUT)
    if (
        type(observation.body) is not bytes
        or type(observation.output_complete) is not bool
        or type(observation.normal_completion) is not bool
        or type(observation.private_capture) is not bool
        or (
            observation.exit_code is not None
            and type(observation.exit_code) is not int
        )
    ):
        return _unknown(Reason.INVALID_INPUT)
    if not observation.private_capture:
        return _unknown(Reason.OUTPUT_NOT_PRIVATE)
    if not observation.normal_completion:
        return _unknown(Reason.COMMAND_NOT_NORMAL)
    if not observation.output_complete:
        return _unknown(Reason.OUTPUT_INCOMPLETE)
    if len(observation.body) > MAX_CAPTURE_BYTES:
        return _unknown(Reason.OUTPUT_OVERSIZE)
    if observation.exit_code not in ALLOWED_EXIT_CODES:
        return _unknown(Reason.EXIT_NOT_ALLOWED)

    payload = observation.body
    # UTF-32LE overlaps the UTF-16LE prefix. Reject it before BOM selection.
    if payload[:4] in (_UTF32LE_BOM, _UTF32BE_BOM):
        return _unknown(Reason.ENCODING_INVALID)

    encoding = "utf-8"  # Unmarked input is strictly UTF-8 (including ASCII).
    had_bom = False
    for bom, bom_encoding in _SUPPORTED_BOMS:
        if payload[: len(bom)] == bom:
            payload = payload[len(bom) :]
            encoding = bom_encoding
            had_bom = True
            break

    if had_bom and any(
        payload[: len(bom)] == bom for bom, _encoding in _SUPPORTED_BOMS
    ):
        return _unknown(Reason.REPEATED_BOM)
    try:
        text = payload.decode(encoding, errors="strict")
    except UnicodeDecodeError:
        return _unknown(Reason.ENCODING_INVALID)

    # Do not case-fold, normalize Unicode, inspect lines, or match a substring.
    body = text.strip(ASCII_OUTER_WHITESPACE)
    if body == ABSENCE_BODY:
        return StatusDecision(Status.ABSENT, Reason.EXACT_ABSENCE_BODY)
    return _unknown(Reason.BODY_NOT_EXACT)
