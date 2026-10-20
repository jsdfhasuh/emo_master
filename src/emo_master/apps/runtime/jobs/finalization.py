"""One terminal attempt and its bounded, exception-free recovery receipt."""
from __future__ import annotations

from dataclasses import dataclass, field
import threading
from typing import Any, Callable
import weakref


def errorText(error: BaseException) -> str:
    """Diagnostics must never interrupt the ownership completion guard."""
    try:
        return str(error)[:512]
    except BaseException:
        return "<unprintable " + type(error).__name__ + ">"


def faultSummary(error: BaseException) -> str:
    return type(error).__name__ + ": " + errorText(error)


@dataclass(eq=False)
class TerminalTicket:
    jobId: str
    changes: dict[str, object]
    ordinal: int
    callback: Callable[[str, str], None] | None
    epoch: int
    owner: weakref.ReferenceType[threading.Thread] | None
    registrationEpoch: int | None = None
    fallback: bool = False
    token: object = field(default_factory=object)
    turn: threading.Event = field(default_factory=threading.Event)
    done: threading.Event = field(default_factory=threading.Event)
    callbackState: str = "NOT_STARTED"
    publication: str = "NOT_ATTEMPTED"
    notification: str = "NOT_ATTEMPTED"
    faults: dict[str, str] = field(default_factory=dict)
    recoveryRunning: bool = False
    waiters: weakref.WeakSet = field(default_factory=weakref.WeakSet)

    def fault(self, phase: str, error: BaseException) -> None:
        self.faults[phase] = faultSummary(error)

    @property
    def incomplete(self) -> bool:
        return (self.callbackState == "ABANDONED"
                or self.publication != "CONFIRMED" or self.notification != "CONFIRMED")

    @property
    def failurePhase(self) -> str:
        if self.callbackState == "ABANDONED":
            return "CALLBACK_NOT_RUN/OWNER_ABANDONED"
        if self.publication != "CONFIRMED":
            return "publication"
        if self.notification != "CONFIRMED":
            return "notification"
        return "callback" if "callback" in self.faults and not self.fallback else ""


@dataclass
class StopOutcome:
    ok: bool
    status: str
    message: str
    error: BaseException | None = None  # Transient caller result, never shared.


@dataclass
class RetirementReceipt:
    processClosed: bool = False
    queueClosed: bool = False
    callbackStarted: bool = False
    callbackDone: bool = False
    faults: dict[str, str] = field(default_factory=dict)
    repair: Callable[[], Any] | None = None

    def fault(self, phase: str, error: BaseException) -> None:
        self.faults[phase] = faultSummary(error)


class AttemptWaiter:
    def __init__(self) -> None:
        self.wake = threading.Event()
        self.cancelled = False
        self.active = True

    def cancel(self) -> None:
        if self.active:
            self.cancelled = True
            self.wake.set()
