"""Check a platform's two source shard manifests without project dependencies."""
import argparse
import json
import os
from pathlib import Path
import sys


def readReport(path, expectedShard):
    report = json.loads(path.read_text(encoding="utf-8"))
    if report["shard"] != expectedShard:
        raise ValueError(f"{path}: expected shard {expectedShard}")
    if report["exitstatus"] != 0:
        raise ValueError(f"{expectedShard}: pytest did not finish successfully")
    collections = {}
    for key in ("collected", "selected", "executed"):
        values = report[key]
        if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
            raise ValueError(f"{expectedShard}: invalid {key} nodeids")
        collections[key] = set(values)
        if len(collections[key]) != len(values):
            raise ValueError(f"{expectedShard}: duplicate {key} nodeids")
    if not collections["selected"]:
        raise ValueError(f"{expectedShard}: no tests selected")
    if collections["executed"] != collections["selected"]:
        raise ValueError(f"{expectedShard}: selected tests were not all executed")
    return collections


def summarize(runtimeReport, otherReport):
    runtime = readReport(runtimeReport, "runtime")
    other = readReport(otherReport, "other")
    if runtime["collected"] != other["collected"]:
        raise ValueError("The shards did not collect the same complete suite")
    if runtime["selected"] & other["selected"]:
        raise ValueError("A test was selected by both shards")
    if runtime["selected"] | other["selected"] != runtime["collected"]:
        raise ValueError("The shard union does not match the complete collection")
    return (f"Source checks: {len(runtime['collected'])} collected cases covered; "
            f"runtime={len(runtime['selected'])}, other={len(other['selected'])}. "
            "Both shards completed successfully; original pytest skips remain skips.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runtime_report", type=Path)
    parser.add_argument("other_report", type=Path)
    args = parser.parse_args(argv)
    try:
        message = summarize(args.runtime_report, args.other_report)
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"[ci-shards] {error}", file=sys.stderr)
        return 1
    print(message)
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(summary).open("a", encoding="utf-8") as stream:
            stream.write(message + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
