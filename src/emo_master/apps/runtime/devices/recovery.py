"""Finite connection-only recovery. Business operations are never replayed."""
from time import monotonic, sleep

from .executor import DeviceError


def connectWithRecovery(driver, definition, cancel, deadline, *, progress=lambda state: None):
    policies = definition.policies
    retries = policies.get("connectionRetryCount", 0)
    recoverySeconds = policies.get("recoveryTimeoutMs", 5000) / 1000
    backoff = policies.get("recoveryBackoffMs", 100) / 1000
    attemptSeconds = policies.get("operationTimeoutMs", 5000) / 1000
    recoveryDeadline = None
    attempt = 0
    unhealthy = True
    while True:
        if cancel():
            raise DeviceError("E_DEVICE_CANCELLED", "connection recovery cancelled before business execution")
        limit = min(deadline, recoveryDeadline) if recoveryDeadline is not None else deadline
        if monotonic() >= limit:
            raise DeviceError("E_DEVICE_RECOVERY_TIMEOUT", "connection recovery total budget exhausted", unhealthy=unhealthy)
        progress(dict(attempt=attempt + 1, state="CONNECTING"))
        try:
            driver.connect(definition.defaults, cancel, min(limit, monotonic() + attemptSeconds))
            return
        except DeviceError as error:
            if (error.outcome != "NOT_EXECUTED" or error.code not in getattr(driver, "connectionRetryCodes", ())
                    or attempt >= retries):
                raise
            if recoveryDeadline is None:
                recoveryDeadline = min(deadline, monotonic() + recoverySeconds)
            if cancel():
                raise DeviceError("E_DEVICE_CANCELLED", "connection recovery cancelled before reset") from error
            driver.resetConnection()
            # Exhausting retries must not turn a clean connection refusal into a resource fault.
            unhealthy = error.unhealthy
            attempt += 1
            progress(dict(attempt=attempt, state="BACKOFF", lastError=str(error)))
            wake = min(recoveryDeadline, monotonic() + backoff)
            while monotonic() < wake and not cancel():
                sleep(min(.01, max(0, wake - monotonic())))


def connectionBudget(policies):
    operation = policies.get("operationTimeoutMs", 5000) / 1000
    recovery = policies.get("recoveryTimeoutMs", 5000) / 1000 if policies.get("connectionRetryCount", 0) else 0
    return min(600, operation + recovery)
