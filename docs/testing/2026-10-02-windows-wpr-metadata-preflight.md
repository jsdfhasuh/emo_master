# Read-only WPR metadata preflight

This separate diagnostic observes a new ephemeral Windows runner. It does not
execute the old controller or native reader, start a session, run pytest/runtime
workloads, change system/diagnostic settings, or install tools/dependencies.
The earlier `255ce58` diagnostic returned the broad `preflight_failed` reason;
that reason spans both pre-start checks and post-start allocation validation.
It does not prove that the earlier attempt never briefly started a session.
Its capture authorization is therefore treated as potentially consumed. This
workflow contains no capture action or capture authorization.

## Exact observations

Using existing runner Python, the installed MSVC compiler, existing WPR, and the
unchanged `scripts/windows_commit_trace.wprp`, it makes these observations once:

1. `wpr -profiles <profile>`: exit status of profile enumeration/validation
2. `wpr -profiledetails <profile>!CommitTrace.Light`: exit status of that profile's details
3. `wpr -status -instancename <fresh UUID name>`: only the expected
   `0xc5583000` status is labeled `instance_absent`; all other codes, including
   zero, are `status_unexpected`, without claiming presence or absence
4. A dedicated native helper calls `QueryAllTracesW` exactly once with 64
   preallocated property buffers, and reports its numeric return status and
   returned count only. It does not inspect returned session names, paths,
   identities, properties, or event data

The profile's SHA-256 remains
`cbb159dc1261ef49abc503861636c0241a22e5538ad803c441862d53dbf1d20b`;
the tool checks it before any WPR invocation. There is no `sessionCheck`, trace
consumer, fallback command, retry, resizing, privilege change, or session cleanup.
The only cleanup deletes directories this invocation created for private files.

Per [Microsoft's API documentation](https://learn.microsoft.com/en-us/windows/win32/api/evntrace/nf-evntrace-queryalltracesw),
the query covers sessions the caller may query and excludes private logging
sessions. The returned value is an API-reported count, not a verified system
total. Success and `ERROR_MORE_DATA` (234) preserve the returned count and expose
whether it reaches/exceeds 64. `ERROR_MORE_DATA` marks the enumeration incomplete;
no expansion or second query follows. Any other API error reports `UNKNOWN`
with a null count and null capacity flags, even if the API changed its count
output parameter. Inconsistent status/count combinations report `UNKNOWN` and
null capacity flags. An inconsistent success count is discarded; an anomalous
`ERROR_MORE_DATA` count is preserved only as its raw returned number, without
claiming validity. Reaching 64 on success does not prove
that Windows has reached a system limit or that a new session cannot be allocated.

Each WPR phase runs independently, so a failed profile command does not hide the
other read-only observations. The helper runs once after those commands. Public
output contains only fixed phase/reason/validity enums, numeric statuses/counts,
and nulls. Raw streams, compiler details, and exception text stay private. The
workflow validates all candidate public lines before displaying any, and removes
its owned temporary directory in `finally`. Abrupt runner termination can skip
cleanup; the ephemeral runner is then the containment boundary. No artifact is
uploaded. Command timeouts are 15 seconds; the workflow ceiling is ten minutes.

## What this can and cannot establish

It distinguishes the profile-listing, profile-detail, and fresh-instance status
branches that the old controller reported as a single `preflight_failed`, and
adds a count-only view of current queryable sessions. It does not repeat the old
source/storage/QPC/volume/deadline checks, metadata-schema checks, or workload.
It cannot establish whether start succeeds, which buffers/flags are allocated,
whether a specific session matches the profile, whether events would be lost,
or why the previous runner failed. Those require a separately authorized future
capture. A new VM cannot reconstruct the earlier runner's session state.

## Offline validation and publication boundary

Run `python -m unittest scripts.diagnostics.windows_wpr_metadata_preflight_fixtures -q`.
These stdlib fixtures mock every WPR/helper process. Where installed `g++` is
available, they additionally compile the actual helper against synthetic Win32
headers and a fake API; no Windows API is called by that harness. They exercise
success, fixed-capacity overflow, unrelated errors with misleading output
parameters, command failures/timeouts, output rejection, private cleanup, and
the exact command/source/workflow boundaries. They do not run the project suite.

The revised workflow requires the exact branch, changed workflow path, parent
`91b2647a78b6e1e59faf5a0d63f7eb41bb55dd26`, first attempt, and commit subject
`fix: preserve WPR profile bytes; read-only metadata 6ca308d1`. Preparation and
offline verification do not publish or run it. Publication follows terminal
normal CI and an independent review; it does not renew capture permission.

## Windows checkout portability and bounded fixture reporting

The first read-only run at `91b2647` stopped in `offline_fixtures` with exit 1,
before native helper compilation or any WPR/API observation. The private fixture
details were removed, so the failing case and actual cause cannot be recovered.
Separately, an offline Git checkout with `core.autocrlf=true` reproducibly changes
the original profile's 28 LF endings to CRLF and fails its byte-identity fixture.
This is a confirmed portability defect, not proof of that run's exact failure.

A single path-specific `.gitattributes` rule now preserves LF on checkout. The
profile's Git blob, actual checked-out bytes, and required SHA-256 stay identical;
the check does not normalize or silently accept different bytes. A controlled
temporary repository tests the failing checkout without the rule and passing
checkout with it. No global/local Git setting is changed.

The new independent `windows_wpr_fixture_report` entry point imports the fixture
module inside private stdout/stderr redirection. Its fixed registry maps the
original 24 cases to IDs 1–24 and five new regression cases to 25–29. The explicit
mapping is in that module's `CASES`; ID 21 checks profile bytes, ID 24 checks the
fake native counter, and ID 25 checks LF preservation. Class setup/teardown IDs
are 101 (metadata), 102 (source boundaries), and 103 (native stub). ID 900 marks
bootstrap/import/loader failure; ID 0 marks an unrecognized case and is invalid.
Before running, the discovered inventory must match the registry exactly once.

Reports contain only fixed numeric IDs, `PASS`/`FAIL`/`ERROR`/`SKIP`/`INVALID`, and
the fixed error categories `NONE`, `ASSERTION`, `OS_ERROR`, `TIMEOUT`, `IMPORT`,
`VALUE`, `TYPE`, or `UNKNOWN`. They never format exception objects, traceback
text, test names, subtest parameters, or skip reasons. Setup, teardown, cleanup,
and subtest failures map to the declared test or class ID. The summary preserves
test, failure, error, skip, and invalid counts. Unknown/missing/duplicate cases
cannot pass. The workflow validates every line before printing any, separately
rejects empty/malformed reports as `fixture_report_invalid`, and requires both
a successful process and an `ok` summary before continuing. Raw output is deleted
with the owned private directory.

The Linux offline suite passes on available Python 3.10, 3.12, and 3.13. The exact
failed runner image's [published inventory](https://github.com/actions/runner-images/blob/win25-vs2026/20260925.250/images/windows/Windows2025-VS2026-Readme.md)
lists default Python 3.12.10 and GCC
15.2.0; actual executable selection, Windows DLL resolution, and MSVC compilation
remain unverified. A discovered `g++` does not itself establish a working native
stub toolchain. Failures or skips there now identify the bounded native case/class
instead of exposing compiler paths or error output. No package is installed to
repair missing tools.
