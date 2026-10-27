"""Persist failure details before a later crash or job cancellation loses JUnit."""
from pathlib import Path


class FailureReports:
    def __init__(self, path):
        self.path = path.resolve()

    def pytest_sessionstart(self, session):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("", encoding="utf-8")

    def pytest_runtest_logreport(self, report):
        if not report.failed:
            return
        lines = [f"[ci-check] {report.nodeid} ({report.when})", report.longreprtext]
        for name, content in report.sections:
            lines.extend((f"--- {name} ---", content))
        detail = "\n".join(lines) + "\n"
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(detail)
        print("\n" + detail, flush=True)


def pytest_configure(config):
    config.pluginmanager.register(FailureReports(Path("build/ci-failures.log")), "ci-failure-reports")
