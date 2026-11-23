"""Validate source shard partition from exact authenticated downloads."""
import argparse
import io
from pathlib import Path
import json
import sys
import xml.etree.ElementTree as ET

from scripts.verify_download import unpack


def collections(verified, shard):
    report = json.loads(verified["ci-shard.json"])
    if report["shard"] != shard or type(report["exitstatus"]) is not int or report["exitstatus"] != 0:
        raise ValueError("source shard failed")
    groups = {}
    for key in ("collected", "selected", "executed"):
        values = report[key]
        if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
            raise ValueError("bad report")
        groups[key] = set(values)
        if len(groups[key]) != len(values):
            raise ValueError("duplicate selection")
    if not groups["selected"] or groups["selected"] != groups["executed"]:
        raise ValueError("incomplete execution")
    # Independent XML is required, and must agree that the workload did not fail.
    xml = verified["ci-results.xml"]
    if b"<!DOCTYPE" in xml.upper() or b"<!ENTITY" in xml.upper():
        raise ValueError("prohibited XML")
    suite = ET.parse(io.BytesIO(xml))
    if not list(suite.iter("testcase")) or next(suite.iter("failure"), None) is not None or next(suite.iter("error"), None) is not None:
        raise ValueError("invalid JUnit status")
    return groups


def check_partition(runtime, other):
    if runtime["collected"] != other["collected"]:
        raise ValueError("different complete collections")
    if runtime["selected"] & other["selected"]:
        raise ValueError("overlapping shards")
    if runtime["selected"] | other["selected"] != runtime["collected"]:
        raise ValueError("incomplete shard union")
    return len(runtime["collected"])


def main():
    p = argparse.ArgumentParser()
    for item in ("age", "identity", "expected", "runtime_zip", "other_zip"):
        p.add_argument(item, type=Path)
    for item in ("runtime_digest", "other_digest"):
        p.add_argument(item)
    a = p.parse_args()
    try:
        expected = json.loads(a.expected.read_text(encoding="ascii"))
        platform = expected.pop("platform")
        required = {"source_sha", "workflow_sha", "run", "attempt", "repository_id"}
        if platform not in {"linux", "windows"} or set(expected) != required:
            raise ValueError("missing binding")
        verified = []
        for shard, archive, digest in (("runtime", a.runtime_zip, a.runtime_digest), ("other", a.other_zip, a.other_digest)):
            binding = dict(expected, suite_family="source", outcome="success", job=f"{platform}-{shard}", packager_sha="")
            data = unpack(archive, digest, a.age, a.identity, binding, verified_members=True)
            verified.append(collections(data, shard))
        count = check_partition(*verified)
    except Exception:  # fixed verdict; no embedded diagnostic or Node IDs in outputs
        print("source_partition=failure")
        return 1
    print(f"source_partition=success collected={count}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
