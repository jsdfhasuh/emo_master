# Scoped WPR status absence: read-only metadata integration

This diagnostic-only delta starts from `425f04d63c453d96f217b43308d7903569761515`.
It changes the existing new-UUID `wpr -status -instancename` query's output
handling. It does not change application code, the trace controller, native trace
reader, WPR profile, native counter, or ordinary CI. It cannot start, stop, cancel,
or clean up a trace, and it does not authorize a later capture.

## Decision and capture boundary

The pure classifier is byte-identical to the independently reviewed offline
prototype, SHA-256
`a9555e3e59c63a5bef06e4e2aa78afe1cc0f0d8a348cd984d6d73521fe88d2d8`.
Its capture fields describe observations; the new adapter establishes them from
the actual subprocess instead of taking caller-supplied flags.

Only `ABSENT` and `UNKNOWN` exist. `ABSENT` means the intended scoped WPR status
query completed and its complete privately captured body matches exactly
`WPR is not recording`, after at most one supported initial BOM and outer ASCII
whitespace. It is not a statement that all ETW sessions are absent or that an old
session was cleaned up. The fixed public metadata row uses `instance_absent` for
this outcome; every rejection uses a fixed `status_*` reason and means UNKNOWN.

- The status child gets `stdin=DEVNULL`, an unbuffered binary stdout pipe, and
  stderr redirected into that same pipe. Nothing is filtered, re-ordered by the
  adapter, decoded early, or matched as a line or substring
- Raw bytes exist only in private process memory. The retained body never exceeds
  4096 bytes. A read may obtain one extra byte solely to reject overflow; the
  excess is never appended. No raw output, path, instance name, or error detail
  appears in the result or public report
- Process completion comes from `poll` plus a successful zero-timeout `wait`.
  Pipe EOF is separate. Windows uses `PeekNamedPipe` for readiness and `ReadFile`
  for bytes; only a failed `ReadFile` with `ERROR_BROKEN_PIPE` and zero bytes is
  EOF. An open empty pipe, successful zero-byte write/read, or process exit alone
  is insufficient. Peek's broken-pipe response is confirmed by a bounded read;
  any residual bytes remain part of the complete body
- A single 15-second monotonic deadline covers process startup, completion and
  draining, including a check after the last read. No blocking reader thread or
  unbounded `communicate` buffer exists. Each ready read is bounded and has no
  competing reader. An inherited writer that never closes cannot yield ABSENT
  Startup time is charged to that budget, but synchronous process creation
  cannot be interrupted by this adapter; a blocked CreateProcess remains subject
  to the workflow's outer 10-minute job timeout. The adapter checks the elapsed
  deadline when process creation returns, rather than promising a 15-second
  interrupt of the operating-system creation call
- Failure cleans up only the immediate status subprocess if still running, with
  at most a one-second wait, then closes our unbuffered read handle. It does not
  inspect or terminate descendants or touch another session. Timeout, overflow,
  read/wait/close errors, missing EOF, and inconsistent API counts remain UNKNOWN
- The classifier accepts the exact unsigned exit values `0` and `0xC5583000` only.
  The adapter does not mask or normalize signed values. Exit code alone never
  establishes absence. Windows `Popen` obtains the process DWORD exit status;
  a Windows synthetic subprocess fixture verifies the high-bit exit value
- Decoding is strict UTF-8, or one explicit UTF-8/UTF-16LE/UTF-16BE BOM. UTF-32,
  repeated/mixed BOMs, invalid bytes, extra stderr, banners, extra lines, localized
  text, Unicode outer whitespace, and any differing body remain UNKNOWN

The original profiles/profile-details commands retain private file outputs and
their prior behavior. The native counter remains one `QueryAllTracesW` call with
capacity 64, the same count validity rules, and no retry or session detail output.

## Offline verification and one-shot workflow

The fixed inventory contains 80 tests: the prior 29 metadata/boundary/native
fixtures (with the two old status expectations updated), the 28 reviewed pure
classifier tests, and 23 new capture/integration fixtures. The fixed-ID wrapper
rejects missing, duplicate, extra, skipped, or unmapped successful-case reports.
The workflow checks that success has exactly IDs 1 through 80, all PASS, with
zero failures, errors, skips or invalid records before running metadata commands.
If the existing portable compiler needed by the native stub fixture is absent,
that skipped fixture now stops the workflow before MSVC/metadata execution. It
does not install a compiler or weaken the required fixture inventory.

The capture fixtures include actual synthetic Python subprocesses on both output
streams, a delayed conflicting tail, an exited parent with an inherited writer,
timeouts, exact/over-cap bodies, strict encodings, Windows DWORD exit status, and
both actual script/module import entrypoints in an explicitly disallowed
environment. Fake subprocess/Win32 fixtures exercise read/wait/close errors,
missing EOF, EOF before completion, zero-length writes, contradictory counts,
and a final read crossing the deadline. None invokes WPR or a trace API.

Run with an existing interpreter, without installing packages:

```sh
PYTHONDONTWRITEBYTECODE=1 python -m unittest -v scripts.diagnostics.windows_wpr_metadata_preflight_fixtures
python -m scripts.diagnostics.windows_wpr_fixture_report /path/to/new/private/report-directory
```

The one-shot workflow is limited to first attempt, exact previous commit
`425f04d63c453d96f217b43308d7903569761515`, and exact subject
`diagnostic: classify scoped WPR status; read-only metadata 9eb237ac`.
It uses only existing runner Python/compiler tools and has no artifact upload,
manual dispatch, installation, capture invocation, or retry. Raw fixtures and
metadata outputs stay private and are removed from the owned temporary directory.

At the time of preparing this change, local offline tests are the available
evidence. The new Windows metadata result is pending the separately authorized
single run. Commit 425's raw status output was already removed, so this change
cannot retrospectively classify it. It does not resolve or alter the older trace
capture gate, collector naming, performance claims, or user-PC verification.

## API references

- [WPR status](https://learn.microsoft.com/en-us/windows-hardware/test/wpt/wpr-command-line-options#status)
  documents scoped recording-status semantics; it does not promise this
  local numeric exit allowlist across every WPR version
- [Microsoft MSO-Scripts](https://github.com/microsoft/MSO-Scripts/blob/main/src/INCLUDE.ps1)
  provides a scoped-status spelling precedent for the exact no-period phrase
  `WPR is not recording` in `GetRunningTraceProviders`. This mutable `main` source
  was checked during the offline parser review on 2026-10-02; no immutable
  revision was verified. Its line-member test is not this stricter whole-body
  acceptance rule and proves nothing about all ETW sessions
- [PeekNamedPipe](https://learn.microsoft.com/en-us/windows/win32/api/namedpipeapi/nf-namedpipeapi-peeknamedpipe)
  supplies readiness without removing bytes
- [ReadFile pipe behavior](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-readfile#pipes)
  distinguishes anonymous-pipe broken EOF from a successful zero-byte read
- [Python subprocess timeout behavior](https://docs.python.org/3/library/subprocess.html#timeout-behavior)
  documents that process creation itself cannot generally be interrupted
