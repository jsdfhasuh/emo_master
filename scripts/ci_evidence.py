"""Seal allowlisted CI evidence. Never write a plaintext archive to disk."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tarfile
from tempfile import NamedTemporaryFile

MIB = 1024 * 1024
FILES = {
    "source": {"pip.stdout.log", "pip.stderr.log", "desktop.stdout.log", "desktop.stderr.log",
               "qt.stdout.log", "qt.stderr.log", "checks.stdout.log", "checks.stderr.log",
               "ci-results.xml", "ci-shard.json", "ci-failures.log", "two-job-observation.json", "status.json"},
    "package": {"product-build.log", "phase.txt", "source-regression.log", "source-regression.xml"},
    "diagnostics": {"stage-report.json", "junit.xml", "observed-junit.xml", "wal-control-junit.xml",
                    "runtime-a18-phase-diagnostics.json", "runtime-capture-diagnostics.json",
                    "export-credit-public.json", "sqlite-append-controls-summary.json",
                    "runtime-sqlite-failure-diagnostics.json", "runtime-sqlite-controls-summary.json",
                    "runtime-stop-diagnostics.json", "runtime-native-thread-origins.json",
                    *(f"step-{i:02}.log" for i in range(3, 9))},
    "probe": {"fixture.log"},
}
MAX_MEMBER = 24 * MIB
MAX_TOTAL = 48 * MIB


def read_regular(path):
    try:
        initial = path.lstat()
    except FileNotFoundError:
        return None
    attrs = getattr(initial, "st_file_attributes", 0)
    if not stat.S_ISREG(initial.st_mode) or initial.st_nlink != 1 or attrs & 0x400:
        raise ValueError("invalid evidence member")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as f:
        current = os.fstat(f.fileno())
        if not os.path.samestat(initial, current) or current.st_size > MAX_MEMBER:
            raise ValueError("changed or oversized evidence")
        data = f.read(MAX_MEMBER + 1)
    if len(data) != initial.st_size:
        raise ValueError("evidence changed")
    return data


def env_value(key, pattern):
    val = os.environ.get(key, "")
    if not re.fullmatch(pattern, val):
        raise ValueError("bad evidence identity")
    return val


def archive(age, kind, source, output, recipient):
    if not re.fullmatch(r"age1[023456789ac-hj-np-z]{58}", recipient):
        raise ValueError("invalid recipient")
    if source.exists() and (source.is_symlink() or getattr(source.lstat(), "st_file_attributes", 0) & 0x400):
        raise ValueError("invalid evidence directory")
    sha = r"[0-9a-f]{40}"
    meta = {
        "version": 1, "suite_family": kind,
        "source_sha": env_value("EMO_EVIDENCE_SOURCE", sha),
        "workflow_sha": env_value("GITHUB_WORKFLOW_SHA", sha),
        "run": env_value("GITHUB_RUN_ID", r"[1-9][0-9]{0,19}"),
        "attempt": env_value("GITHUB_RUN_ATTEMPT", r"[1-9][0-9]{0,5}"),
        "repository_id": env_value("GITHUB_REPOSITORY_ID", r"[1-9][0-9]{0,19}"),
        "job": env_value("EMO_EVIDENCE_JOB", r"[A-Za-z0-9_-]{1,100}"),
        "outcome": env_value("EMO_EVIDENCE_OUTCOME", r"success|failure|cancelled|skipped"),
        "packager_sha": env_value("EMO_EVIDENCE_PACKAGER", rf"(?:{sha})?"),
        "files": {},
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.unlink(missing_ok=True)
    temp = NamedTemporaryFile(dir=output.parent, delete=False)
    temp_name = Path(temp.name)
    temp.close()
    try:
        child = subprocess.Popen([str(age), "-r", recipient, "-o", str(temp_name)], stdin=subprocess.PIPE,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            with child.stdin as write, tarfile.open(fileobj=write, mode="w|") as tar:
                def add(name, data):
                    member = tarfile.TarInfo(name)
                    member.size = len(data)
                    member.mode = 0o600
                    tar.addfile(member, io.BytesIO(data))

                total = 0
                for name in sorted(FILES[kind]):
                    data = read_regular(source / name)
                    if data is None:
                        continue
                    total += len(data)
                    if total > MAX_TOTAL:
                        raise ValueError("oversized evidence")
                    meta["files"][name] = {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                    add(name, data)
                if kind == "diagnostics" and "stage-report.json" not in meta["files"]:
                    raise ValueError("missing diagnostic stage report")
                add("manifest.json", json.dumps(meta, sort_keys=True).encode("ascii"))
            if child.wait(timeout=90):
                raise ValueError("age rejected encryption")
        finally:
            if child.poll() is None:
                child.kill()
                child.wait()
        with temp_name.open("rb") as handle:
            if handle.readline(40) != b"age-encryption.org/v1\n":
                raise ValueError("ciphertext header missing")
        temp_name.replace(output)
    finally:
        temp_name.unlink(missing_ok=True)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("kind", choices=FILES)
    p.add_argument("source", type=Path)
    p.add_argument("output", type=Path)
    p.add_argument("age", type=Path)
    args = p.parse_args()
    try:
        archive(args.age, args.kind, args.source, args.output, os.environ.get("CI_EVIDENCE_RECIPIENT", ""))
    except (OSError, ValueError, subprocess.SubprocessError, tarfile.TarError):
        print("evidence_encryption=failure")
        return 1
    print("evidence_encryption=success")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
