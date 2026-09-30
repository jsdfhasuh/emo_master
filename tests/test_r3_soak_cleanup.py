"""Measurement failure cleanup must still attempt every independently owned resource."""
from types import SimpleNamespace

from scripts.r3_soak import disposeGuiHub, retireOwned


def testFailedObserverCloseDoesNotPreventStoppingOwnedSyntheticRuntime():
    calls = []

    def fail():
        calls.append("client")
        raise RuntimeError("injected observer close failure")

    runtime = SimpleNamespace(StopJob=lambda request, context: calls.append(("stop", request.job_id)),
                              close=lambda: calls.append("runtime"))
    errors = retireOwned([SimpleNamespace(close=lambda: calls.append("window"))],
        [SimpleNamespace(close=fail)], runtime, SimpleNamespace(close=lambda: calls.append("server")), "synthetic")
    assert calls == ["window", "client", ("stop", "synthetic"), "server", "runtime"]
    assert len(errors) == 1 and errors[0]["owner"] == "client-0"
    assert "injected observer" in errors[0]["error"]


def testSoakGuiHubAndItsTimerAreNativelyRetired(retainedQtApplication):
    from shiboken2 import isValid
    from emo_master.ui.presentation.hub import DisplayHub
    hub = DisplayHub(SimpleNamespace())
    timer = hub.timer
    assert isValid(hub) and isValid(timer)
    disposeGuiHub(hub)
    assert not isValid(hub) and not isValid(timer)
