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

The workflow requires the exact branch, changed workflow path, parent
`255ce58de8a9464f853ab563612e21f369331a95`, first attempt, and commit subject
`diagnostic: read-only WPR metadata preflight bb2756e4`. Preparation and offline
verification do not publish or run it. Publication follows terminal normal CI
and an independent review; it does not renew capture permission.
