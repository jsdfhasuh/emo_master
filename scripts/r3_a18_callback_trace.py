"""One original A18 call with creation stacks, loader and Thread Pool events.

Temporary diagnostic on one Windows Actions branch. Classic Thread Pool events
span the disposable runner; correlate by PID/TID after capture, never claim a
process filter. Reuses the existing WPR lifecycle and bounded phase observer.
"""
import json
import os
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from scripts.r3_a18_creation_trace import Trace
from scripts.r3_a18_thread_diagnostics import PhaseOwner, PHASE_LIMITATIONS, TARGET
from scripts.r3_threadpool_provider_smoke import PROFILE, providerSessions


INVALID = pytest.StashKey()


def enabled():
    return (os.name == "nt" and os.environ.get("GITHUB_ACTIONS") == "true"
            and os.environ.get("RUNNER_OS") == "Windows"
            and os.environ.get("GITHUB_REF") == "refs/heads/codex/ci-threadpool-provider-smoke"
            and os.environ.get("EMO_A18_CALLBACK_TRACE") == "1")


def combinedProfile():
    tree = ET.parse(Path(__file__).with_name("a18_thread_creation.wprp"))
    profiles = tree.getroot().find("Profiles")
    callback = ET.fromstring(PROFILE).find("Profiles")
    profiles.insert(1, callback.find("EventCollector"))
    profiles.insert(3, callback.find("EventProvider"))
    profiles.find("Profile/Collectors").append(callback.find("Profile/Collectors/EventCollectorId"))
    return ET.tostring(tree.getroot(), encoding="unicode")


@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item):
    if item.nodeid != TARGET or not enabled():
        return (yield)
    trace, owner = None, None
    errors = []
    try:
        try:
            before = providerSessions()
            if any(row["enabled"] for row in before):
                raise RuntimeError("classic provider already enabled")
            trace = Trace()
            trace.root = Path("build/a18-callback-trace") / trace.instance
            trace.root.parent.mkdir(parents=True, exist_ok=True)
            trace.profile = (trace.root.parent / "callback.wprp").resolve()
            trace.profile.write_text(combinedProfile(), encoding="utf-8")
            trace.record.update(role="original_a18_callback_diagnostic", source=os.environ.get("GITHUB_SHA"),
                provider_sessions_before=before,
                limits="32 MiB kernel + 4 MiB Thread Pool memory; one original test call. "
                       "No sampling/file/network providers. Classic events span the disposable runner.")
            trace.start()
            if trace.started:
                owner = PhaseOwner(item.module, trace.root / "thread-origins.json")
                owner.install()
        except BaseException as error:
            errors.append("setup_" + type(error).__name__)
        return (yield)
    finally:
        if owner is not None:
            try:
                if not owner.retire():
                    errors.append("phase_observer_not_retired")
            except BaseException as error:
                errors.append("phase_retire_" + type(error).__name__)
        if trace is not None:
            try:
                trace.finish()
                trace.record["provider_sessions_after"] = providerSessions()
                if any(row["enabled"] for row in trace.record["provider_sessions_after"]):
                    errors.append("provider_still_enabled")
                (trace.root / "metadata.json").write_text(json.dumps(trace.record, indent=2) + "\n", encoding="utf-8")
            except BaseException as error:
                errors.append("trace_finish_" + type(error).__name__)
            errors.extend(trace.record["errors"])
            if not trace.record["retired"]:
                errors.append("trace_retirement_unconfirmed")
        if owner is not None:
            try:
                owner.save()
                (trace.root / "checkpoints.json").write_text(json.dumps({
                    "role": "diagnostic_only", "pid": trace.record["pid"],
                    "checkpoints": owner.rows, "checkpoint_calls": owner.calls,
                    "omitted_checkpoints": owner.dropped, "errors": owner.errors,
                    "observer_retired": not owner.patches, "limitations": PHASE_LIMITATIONS,
                }, indent=2) + "\n", encoding="utf-8")
            except BaseException as error:
                errors.append("phase_save_" + type(error).__name__)
            errors.extend(owner.errors)
        if errors:
            item.config.stash[INVALID] = errors
        print("A18_CALLBACK_TRACE " + json.dumps({"path": str(trace.root) if trace else None,
                                                  "errors": errors}), flush=True)


def pytest_sessionfinish(session):
    errors = session.config.stash.get(INVALID, [])
    if errors:
        print("A18_CALLBACK_TRACE_INVALID " + json.dumps(errors), flush=True)
        if session.exitstatus == 0:
            session.exitstatus = pytest.ExitCode.TESTS_FAILED
