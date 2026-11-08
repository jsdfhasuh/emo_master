"""Opt-in, file-based CI shards; the normal pytest entry keeps full collection."""
from pathlib import Path
import json

import pytest


SHARDS = ("designer-ui", "runtime-other")


def pytest_addoption(parser):
    group = parser.getgroup("source CI shards")
    group.addoption("--ci-shard", choices=SHARDS)
    group.addoption("--ci-shard-report", type=Path)


def pytest_configure(config):
    shard = config.getoption("ci_shard")
    report = config.getoption("ci_shard_report")
    if bool(shard) != bool(report):
        raise pytest.UsageError("--ci-shard and --ci-shard-report must be used together")
    if shard:
        config.pluginmanager.register(SourceShard(shard, report), "source-ci-shard")


class SourceShard:
    def __init__(self, shard, report):
        self.report = report
        self.data = {"shard": shard, "collected": [], "selected": [], "executed": [],
                     "exitstatus": None}

    @pytest.hookimpl(trylast=True)
    def pytest_collection_modifyitems(self, config, items):
        self.data["collected"] = [item.nodeid for item in items]
        selected, deselected = [], []
        for item in items:
            parts = item.path.relative_to(config.rootpath).parts
            designerUi = parts[:2] in (("tests", "designer"), ("tests", "ui"))
            matches = designerUi == (self.data["shard"] == "designer-ui")
            (selected if matches else deselected).append(item)
        self.data["selected"] = [item.nodeid for item in selected]
        config.hook.pytest_deselected(items=deselected)
        items[:] = selected

    def pytest_runtest_logstart(self, nodeid, location):
        # Setup-skipped cases also enter the runner; collect-only cases do not.
        self.data["executed"].append(nodeid)

    def pytest_sessionfinish(self, session, exitstatus):
        self.data["exitstatus"] = int(exitstatus)
        self.report.parent.mkdir(parents=True, exist_ok=True)
        self.report.write_text(json.dumps(self.data, ensure_ascii=False, indent=2) + "\n",
                               encoding="utf-8")
