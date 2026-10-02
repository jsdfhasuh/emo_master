"""Read-only WPR metadata probes. No controller, capture, or runtime imports.

Only fixed-schema aggregate rows reach stdout. Command output and exceptions
are private; the caller must also keep this process's stderr private.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import uuid

PROFILE_SHA256 = "cbb159dc1261ef49abc503861636c0241a22e5538ad803c441862d53dbf1d20b"
ABSENT_STATUS = 0xC5583000
MORE_DATA = 234
CAPACITY = 64
MAX_OUTPUT = 4 * 1024 * 1024
TIMEOUT = 15
PHASES = frozenset(("environment", "private_directory", "profile_identity", "profiles",
                    "profile_details", "instance_status", "session_count", "cleanup", "complete"))
REASONS = frozenset(("ok", "wrong_environment", "private_directory_failed", "profile_changed",
                     "capability_missing", "command_failed", "command_timeout", "output_limit",
                     "instance_absent", "status_unexpected", "native_output_invalid", "more_data",
                     "api_error", "capacity_reached", "count_inconsistent", "cleanup_failed",
                     "metadata_inconclusive", "unexpected_failure"))


def row(phase, reason, native_exit=None, api_status=None, returned_count=None):
    """Never pass through a caller-provided string, collection, or boolean."""
    if phase not in PHASES or reason not in REASONS:
        raise ValueError("invalid_enum")
    for value in (native_exit, api_status, returned_count):
        if value is not None and (type(value) is not int or not 0 <= value <= 0xFFFFFFFF):
            raise ValueError("invalid_number")
    reported = phase == "session_count" and api_status in (0, MORE_DATA) and returned_count is not None
    known = reported and reason != "count_inconsistent"
    # Preserve ERROR_MORE_DATA's output parameter even in an anomalous result,
    # but never derive capacity flags from inconsistent status/count evidence.
    preserve = known or (reported and api_status == MORE_DATA)
    return {"phase": phase, "reason": reason, "native_exit": native_exit,
            "api_status": api_status, "returned_count": returned_count if preserve else None,
            "count_validity": "API_REPORTED" if known else "UNKNOWN",
            "capacity": CAPACITY if phase == "session_count" else None,
            "capacity_reached": int(returned_count >= CAPACITY) if known else None,
            "capacity_exceeded": int(returned_count > CAPACITY) if known else None}


def emit(result):
    # Rebuild every field so extra keys / native text cannot escape.
    safe = row(*(result[key] for key in ("phase", "reason", "native_exit", "api_status", "returned_count")))
    print("WPR_METADATA " + json.dumps(safe, separators=(",", ":")), flush=True)


def command(argv, root, phase):
    """One command, private streams, bounded duration. Never retry a command."""
    output = root / (phase + ".private")
    try:
        with output.open("xb") as stream:
            result = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=stream, stderr=stream,
                                    timeout=TIMEOUT, check=False, shell=False)
        status = int(result.returncode) & 0xFFFFFFFF
        if output.stat().st_size > MAX_OUTPUT:
            return row(phase, "output_limit", status), None
        return row(phase, "ok", status), output
    except subprocess.TimeoutExpired:
        return row(phase, "command_timeout"), None
    except BaseException:
        return row(phase, "command_failed"), None


def session_count(helper, root):
    result, output = command([str(helper)], root, "session_count")
    if result["reason"] != "ok":
        return result
    if result["native_exit"] != 0:
        return row("session_count", "command_failed", result["native_exit"])
    try:
        raw = output.read_bytes()
        if len(raw) > 256:
            raise ValueError("native_output_invalid")
        def unique(pairs):
            values = dict(pairs)
            if len(values) != len(pairs):
                raise ValueError("duplicate_keys")
            return values
        data = json.loads(raw.decode("ascii"), object_pairs_hook=unique)
        if type(data) is not dict or set(data) != {"api_status", "returned_count"}:
            raise ValueError("native_output_invalid")
        status, count = data["api_status"], data["returned_count"]
        if type(status) is not int or not 0 <= status <= 0xFFFFFFFF:
            raise ValueError("native_output_invalid")
        if status not in (0, MORE_DATA):
            # Ignore any returned count, even if a malformed helper supplied it.
            return row("session_count", "api_error", 0, status)
        if type(count) is not int or not 0 <= count <= 0xFFFFFFFF:
            raise ValueError("native_output_invalid")
        reason = "more_data" if status == MORE_DATA else "ok"
        if status == 0 and count == CAPACITY:
            reason = "capacity_reached"
        if (status == 0 and count > CAPACITY) or (status == MORE_DATA and count <= CAPACITY):
            reason = "count_inconsistent"
        return row("session_count", reason, 0, status, count)
    except BaseException:
        return row("session_count", "native_output_invalid", 0)


def inspect(root, helper, profile, wpr):
    """Independent metadata observations continue after a failed observation."""
    failed = False
    try:
        profile_valid = hashlib.sha256(profile.read_bytes()).hexdigest() == PROFILE_SHA256
    except BaseException:
        profile_valid = False
    if not profile_valid:
        emit(row("profile_identity", "profile_changed"))
        return 2
    # A new cryptographic UUID is never printed and is used only for this query.
    instance = "R3ReadOnly_" + uuid.uuid4().hex
    commands = (
        ("profiles", [str(wpr), "-profiles", str(profile)]),
        ("profile_details", [str(wpr), "-profiledetails", str(profile) + "!CommitTrace.Light"]),
        ("instance_status", [str(wpr), "-status", "-instancename", instance]),
    )
    for phase, argv in commands:
        if not wpr.is_file():
            result = row(phase, "capability_missing")
        else:
            result, _ = command(argv, root, phase)
            if result["reason"] == "ok":
                status = result["native_exit"]
                if phase == "instance_status":
                    result = row(phase, "instance_absent" if status == ABSENT_STATUS else "status_unexpected", status)
                elif status != 0:
                    result = row(phase, "command_failed", status)
        emit(result)
        failed |= result["reason"] not in ("ok", "instance_absent")
    result = session_count(helper, root) if helper.is_file() else row("session_count", "capability_missing")
    emit(result)
    failed |= result["reason"] not in ("ok", "capacity_reached")
    return 2 if failed else 0


def main(args):
    if (len(args) != 1 or os.name != "nt" or os.environ.get("GITHUB_ACTIONS") != "true"
            or os.environ.get("RUNNER_OS") != "Windows" or os.environ.get("GITHUB_RUN_ATTEMPT") != "1"):
        emit(row("environment", "wrong_environment"))
        return 2
    parent = Path(args[0])
    helper = parent / "session-count.exe"
    profile = Path(__file__).resolve().with_name("windows_commit_trace.wprp")
    wpr = Path(os.environ.get("SystemRoot", "")) / "System32" / "wpr.exe"
    root = None
    result = 2
    try:
        # No reuse, no recursive deletion of caller-supplied parent directories.
        root = Path(tempfile.mkdtemp(prefix="metadata-", dir=parent))
        result = inspect(root, helper, profile, wpr)
    except BaseException:
        emit(row("private_directory" if root is None else "complete",
                 "private_directory_failed" if root is None else "unexpected_failure"))
    finally:
        if root is not None:
            try:
                shutil.rmtree(root)
                emit(row("cleanup", "ok"))
            except BaseException:
                emit(row("cleanup", "cleanup_failed"))
                result = 2
    emit(row("complete", "ok" if result == 0 else "metadata_inconclusive"))
    return result


if __name__ == "__main__":
    try:
        code = main(sys.argv[1:])
    except BaseException:
        emit(row("complete", "unexpected_failure"))
        code = 2
    raise SystemExit(code)
