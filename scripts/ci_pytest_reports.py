"""Persist failure details before a later crash or job cancellation loses JUnit."""
from pathlib import Path
import json

import pytest


NATIVE_OWNER_TEST = ("tests/ui/presentation/test_retained_acceptance_lifecycle.py::"
                     "testThousandNavigationsAndThirtyFloatingCyclesRetireNativeOwners")


def nativeOwnerFailureEvidence(excinfo):
    """Read only the failed A18 snapshots; never take a replacement census."""
    from scripts.r3_native_thread_origins import collect, LIMITATIONS, MAX_BYTES, MAX_THREADS

    target = Path(__file__).resolve().parents[1] / NATIVE_OWNER_TEST.split("::")[0]
    frames = [entry for entry in excinfo.traceback if entry.path == target]
    context = next((entry.locals for entry in reversed(frames)
                    if entry.name in ("assertStableOwners", "assertNoNewThreadsAtPhase")), {})
    for entry in reversed(frames):
        if entry.name not in ("assertStableOwners", "assertNoNewThreads", NATIVE_OWNER_TEST.split("::")[1]):
            continue
        before = entry.locals.get("before" if entry.name != NATIVE_OWNER_TEST.split("::")[1] else "baseline")
        after = entry.locals.get("after")
        if not isinstance(before, dict) or not isinstance(after, dict):
            continue
        ids = sorted(after["native_thread_ids"] - before["native_thread_ids"])
        counts = ("handles", "native_threads", "python_threads", "widgets", "windows")
        record = {
            "role": "diagnostic_only", "trigger": "failed_call_after_cleanup",
            "failure_site": entry.name, "phase": context.get("phase"), "index": context.get("index"),
            "before": {key: before[key] for key in counts if type(before.get(key)) is int},
            "after": {key: after[key] for key in counts if type(after.get(key)) is int},
            "new_native_thread_count": len(ids), "selected_native_thread_ids": ids[:MAX_THREADS],
            "limitations": "Collected after the test call and its finally cleanup. " + LIMITATIONS,
            "evidence": collect(ids[:MAX_THREADS]),
        }
        content = json.dumps(record, ensure_ascii=True)
        if len(content.encode("ascii")) > MAX_BYTES:
            record["evidence"] = {"status": "output_limit", "threads": []}
            content = json.dumps(record, ensure_ascii=True)
        return content
    return '{"status":"failed_snapshot_unavailable","role":"diagnostic_only"}'


class FailureReports:
    def __init__(self, path):
        self.path = path.resolve()

    def pytest_sessionstart(self, session):
        self.terminalWriter = session.config.get_terminal_writer()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("", encoding="utf-8")

    @pytest.hookimpl(wrapper=True)
    def pytest_runtest_makereport(self, item, call):
        report = yield
        if (item.nodeid == NATIVE_OWNER_TEST and report.when == "call"
                and report.failed and call.excinfo is not None):
            try:
                evidence = nativeOwnerFailureEvidence(call.excinfo)
            except BaseException:
                # Diagnostics must never replace the original failure/report.
                evidence = '{"status":"diagnostic_unavailable","role":"diagnostic_only"}'
            report.sections.append(("A18 post-failure native origins", evidence))
        return report

    def pytest_runtest_logreport(self, report):
        if not report.failed:
            return
        lines = [f"[ci-check] {report.nodeid} ({report.when})", report.longreprtext]
        for name, content in report.sections:
            lines.extend((f"--- {name} ---", content))
        detail = "\n".join(lines) + "\n"
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(detail)
        self.terminalWriter.write("\n" + detail, flush=True)


def pytest_configure(config):
    config.pluginmanager.register(FailureReports(Path("build/ci-failures.log")), "ci-failure-reports")
