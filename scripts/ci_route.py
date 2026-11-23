"""Route verified product events against the existing workflow trigger table.

This offline helper does not authenticate events, fetch GitHub data, trigger
workflows, or declare a check successful. Callers must verify the event and
supply the original PR base / push ref and changed paths. None means that the
changed paths are unknown; in that case do not silently skip matching suites.
"""
from collections.abc import Iterable, Mapping
from functools import lru_cache
from pathlib import Path
import re

import yaml


ACTIVE = (
    "ci", "web-panel-ci", "display-session-contract", "runtime-a18-diagnostics",
    "runtime-capture-diagnostics", "runtime-credit-diagnostics",
    "runtime-sqlite-append-controls", "runtime-sqlite-failure-diagnostics",
    "runtime-stop-diagnostics", "runtime-windows", "validate-windows-package",
    "release-windows",
)
ARCHITECTURE_REF = "refs/heads/agent/runtime-workflow-architecture-v1"


@lru_cache(maxsize=128)
def _pattern(glob: str) -> re.Pattern[str]:
    """Support the positive glob forms used by these workflows, not gitignore."""
    if any(char in glob for char in "![]+?\\") or "***" in glob:
        raise ValueError("unsupported legacy workflow glob")
    return re.compile("".join(".*" if part == "**" else "[^/]*" if part == "*"
                              else re.escape(part) for part in re.split(r"(\*\*|\*)", glob)))


def _matches(value: str, patterns: Iterable[str]) -> bool:
    return any(_pattern(glob).fullmatch(value) for glob in patterns)


def load_trigger_table(directory: Path) -> dict[str, Mapping[str, object]]:
    table = {}
    for suite in ACTIVE:
        path = directory / f"{suite}.yml"
        workflow = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
        triggers = workflow["on"]  # GitHub 'on' is a string, not a YAML 1.1 boolean.
        if not isinstance(triggers, dict) or set(triggers) - {"push", "pull_request", "workflow_dispatch"}:
            raise ValueError("unsupported legacy workflow event")
        for event, rule in triggers.items():
            allowed = {"inputs"} if event == "workflow_dispatch" else {"branches", "tags", "paths"}
            if rule != "" and (not isinstance(rule, dict) or set(rule) - allowed):
                raise ValueError("unsupported legacy workflow filter")
        table[suite] = triggers
    return table


def route_verified_event(
    table: Mapping[str, Mapping[str, object]], *, event: str, ref: str,
    paths: Iterable[str] | None = None, requested: str | None = None,
) -> tuple[str, ...]:
    """Return suites selected by the original event; manual dispatch selects one."""
    if event == "workflow_dispatch":
        if requested not in table or "workflow_dispatch" not in table[requested]:
            raise ValueError("unsupported manual suite")
        if requested == "runtime-credit-diagnostics" and ref != ARCHITECTURE_REF:
            return ()
        return (requested,)
    if requested is not None:
        raise ValueError("suite override is manual-only")
    if event not in ("push", "pull_request"):
        return ()
    prefix = "refs/tags/" if event == "push" and ref.startswith("refs/tags/") else "refs/heads/"
    if not ref.startswith(prefix):
        raise ValueError("pass the product branch (PR base) or the push tag")
    name = ref[len(prefix):]
    if not name:
        raise ValueError("empty product ref")
    scope = "tags" if prefix == "refs/tags/" else "branches"
    changed = None if paths is None else tuple(paths)
    selected = []
    for suite, triggers in table.items():
        rule = triggers.get(event)
        if not isinstance(rule, dict) or not _matches(name, rule.get(scope, ())):
            continue
        if scope == "branches" and "paths" in rule and changed is not None:
            if not any(_matches(path, rule["paths"]) for path in changed):
                continue
        selected.append(suite)
    return tuple(selected)
