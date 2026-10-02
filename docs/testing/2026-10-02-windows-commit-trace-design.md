# One approved Windows kernel trace (DIAGNOSTIC ONLY)

Base: `655eeac84d0602aa670c76af67679509782bbd7f`. The isolated candidate
contains five new diagnostic files and this document. Production code, existing
tests, SQLite durability, original assertion/wait deadlines and pytest ordering
are unchanged. No concurrent-Subscribe candidate is included. No user's computer
is used. Publication and the real capture require independent review first.

## One-attempt execution gate

The new workflow runs only on the existing
`agent/runtime-workflow-architecture-v1` branch, when its own path changes,
`github.run_attempt == 1`, the push's `before` SHA is exactly the base above,
and the head commit message is exactly:

`diagnostic: one Windows kernel trace 655eeac f2b07937`

There is no dispatch, schedule, pull-request trigger or automatic retry. The
controller independently verifies the Git parent, exact message, clean tracked
source and changed-file allowlist. It verifies the source again before declaring
valid evidence. A later ordinary commit/merge does not meet the gate. No capability
check or cleanup command starts a capture. The controller has one start site.

The user separately approved the existing CI Python/project-dependency setup.
That does not authorize installing tracing tools, SDKs, drivers or system
components. Checkout has no persisted credentials and only contents-read
permission. No repository secrets are referenced.

## Files

- `.github/workflows/runtime-kernel-trace-diagnostics.yml`: one `windows-latest`
  job, 30-minute maximum including setup and cleanup; original CI dependency
  setup; SDK-only reader build; explicit offline fixtures and native metadata
  preflight before the only capture; independent validity and cleanup steps
- `scripts/windows_commit_trace.wprp`: memory mode, requested 64 KiB × 4096
  buffers (256 MiB circular recording pool); only ProcessThread, CSwitch,
  ReadyThread, FileIO, FileIOInit, Filename, DiskIO and DiskIOInit
- `scripts/windows_commit_trace_reader.cpp`: existing SDK/TDH/OpenTrace/
  ProcessTrace/CloseTrace; raw timestamp mode; strict required-field decoding;
  read-only native schema and actual allocation checks; no event data on stdout
- `scripts/r3_windows_commit_trace.py`: opt-in pytest observer, finite controller,
  normalized-event reducer and freshly constructed public allowlist
- `scripts/diagnostics/windows_commit_trace_fixtures.py`: explicitly invoked
  offline fixtures, outside default pytest collection

No GeneralProfile, CPU sampling, stacks, network, registry or heap keywords are
requested. WPR can include automatic metadata/rundown; those raw details stay
private. There is no undocumented privacy switch or broader fallback profile.
The normative WPR schema spells the keyword `DiskIOInit`; a descriptive Microsoft
page says `DiskIOInitialization`. Installed WPR profile validation is mandatory.

## Exact observation scope

1. `tests/runtime/presentation/test_image_demand_client.py::testZeroDemandKeepsFormalResultsAndResumesFinalCapturedResult[presentation]`
2. `tests/runtime/presentation/test_multi_capture.py::testLegacyReaderIgnoringExpiryUsesResetToFenceDelayedDecode`

The existing detailed-SQL plugin does not cover case 2. This separate plugin is
mutually exclusive with the existing SQL/thread probes. For each target it installs
one class-level `_connect` wrapper around setup/call/teardown and restores it
before the next test. It uses the existing native connection adapter, forwarding
original operations once. Only commit/context-exit boundaries acquire direct
QueryPerformanceCounter ticks and native TID. There are no extra SQL, transactions,
WAL-header reads, connection closes, busy-timeout changes or nested SQL probes.

Each case has at most 32 store lifetimes and 512 boundary intervals. Omission,
installation/retirement failure, missing target membership, mismatched original
source, or an unobserved connection lifetime makes evidence incomplete. Diagnostic
timing failures preserve the native return/exception. An already-entered adapter
keeps its original case owner after retirement; it is never reassigned to a later
case. The observer does not alter pytest's exit status.

The first failure signal is the first failed target pytest report, including setup,
call or teardown. A call report occurs **after the test body's own finally**. This
is not a promise to stop before body cleanup. Trace stop does not stop/reorder the
remaining original suite. If both exact target protocols finish teardown and
observer retirement first, collection stops at that scope-complete signal while
the remaining original tests continue. Cases beginning after trace stop are explicitly outside
capture. Calls open at stop have an explicitly truncated observation endpoint and
`returned_in_capture=false`; no native return time is invented.

## Bounds, ownership and privacy

The entire job is limited to 30 minutes as final containment. The first step saves
a 29-minute internal budget. Before starting capture at least five minutes must
remain. The controller requests stop no later than the lesser of 27 minutes after
start or five minutes before that internal deadline, reserving bounded stop,
retirement, parsing and cleanup time. WPR stop is limited to 90 seconds, cancel to
15 seconds, and complete native decoding plus Python reduction to 120 seconds.
The suite gets the remaining budget with a three-minute reserve; a guard-killed
suite has no claimed original pytest exit. Original individual test deadlines are
unchanged. The external diagnostic job can be incomplete rather than relax them.

A unique run-specific WPR instance is checked absent before start. An attempted
start is treated as possibly active even if the start command fails/times out.
Only that owned instance may be stopped/cancelled. Actual absence is verified after
stop/cancel. On unconfirmed cancellation the private directory and ownership
marker remain for an independent, bounded cleanup step. The latter also verifies
absence before removal; it never cancels another instance. No OS shutdown or
security/privilege changes are used.

256 MiB bounds the requested circular recording pool, **not** final merged ETL
size, total merge memory or total runner storage. The native read-only query checks
actual buffering mode, QPC clock, zero loss, the exact allowed classic kernel
EnableFlags (with optional NO_SYSCONFIG suppression only), and actual `NumberOfBuffers ×
BufferSize` within 256 MiB (MaximumBuffers is ignored by ETW in buffering mode).
Missing/unrecognized session allocation is a stop condition, not permission to
change capture methods. Installed WPR naming/metadata behavior remains a Windows
capability check; no second trace is authorized to calibrate it.

Additional guards: at least 2 GiB free disk, 1 GiB per ETL, 768 MiB decoded records,
8 million decoded records, 32 MiB each for captured native/test output, 2 GiB total
owned-directory size, bounded live maps and 256 KiB final public JSON. File-size
checks are observed watchdog limits, not atomic filesystem hard caps. Raw paths,
command output, stdout/stderr, ETL, normalized records and metadata remain only in
the owned runner-temp directory. No XML is produced. No raw upload, artifact,
cache, print or external copy is configured. Removal is attempted in finally and
again by the independent always-run cleanup. If the job/VM is terminated before
cleanup, ephemeral runner disposal is the final containment boundary; cleanup is
not falsely declared successful.

## Validity and interpretation

The reader uses `PROCESS_TRACE_MODE_EVENT_RECORD | PROCESS_TRACE_MODE_RAW_TIMESTAMP`.
It requires a finalized QPC trace, matching QueryPerformanceFrequency, zero lost
events/buffers and complete decoding. Required schemas have explicit reviewed
provider/opcode/version bounds and TDH property-name/type/width validation. Unknown
required schemas stop parsing. Native metadata preflight uses synthetic descriptors,
not a calibration trace. Actual observed schema/event coverage is checked after
the single capture; synthetic success is never substituted for real coverage.

Process creation after collection begins and the exact pytest PID delimit process
identity. Every observed SQL TID must have an unambiguous process-owned creation
and lifetime. PID/TID reuse or missing beginnings invalidates attribution. Every
CPU must have a retained scheduling prefix before target process creation, and its
subsequent switch old/new chain must remain coherent. Running on two CPUs,
impossible transitions, lost events or unproven circular retention is INVALID.
Equal-QPC migrations remove old runners across CPUs before installing new runners.
ReadyThread uses payload `TThreadId`, never its header PID/TID. Switch-out waiting
state and later ReadyThread divide blocked and ready; unestablished states remain
unknown. Running means **scheduled-running**, with unaccounted ISR/DPC time. It is
not exact native CPU time or proof of a GIL/lock/fsync cause.

DB/WAL matches use exact observed paths and a verified local volume mapping, never
basenames or temporal coincidence. File-object creation/close and IRP begin/end
pairing delimit reuse. Watched-thread flushes with missing identity invalidate a
claim of complete absence. Rename/delete/name conflicts, duplicate live IRPs,
reused live objects and mismatched completions invalidate pairing. Completion
header threads are not used for attribution. Physical disk flushes are a separate
coverage count; they are not joined to a DB merely through overlap/TID, are not
added to scheduling durations, and are not durability proof.

The public report only contains constant case IDs, case/capture outcomes, relative
microsecond intervals, scheduled running/ready/blocked/unknown totals, matched
DB/WAL flush overlays/status/counts, original pytest exit/completeness, fixed
coverage/loss/reason fields and cleanup validity. Every public field is rebuilt
and checked against a closed schema. No raw IDs, pointers, paths, command lines,
environment, unrelated process names, addresses or arbitrary exception text may
be emitted. A valid or passing instrumented run is diagnostic only, not acceptance
and not proof that uninstrumented Windows is fixed.

## Offline validation and remaining real capability checks

Current offline fixtures cover correct scheduling and DB flush pairing, ReadyThread
payload attribution, completion from another header thread, per-CPU gaps,
equal-timestamp migration, PID/TID/file/IRP identity gaps, rename and key conflicts,
loss, invalid QPC, missing schemas, explicit partial-call endpoints, output
injection, retired adapters, clock failures preserving native exceptions,
one-shot trigger/scope, uncertain start, failed cancellation preserving cleanup
ownership, original exit preservation and killed-suite incompleteness.

Actual Windows MSVC compilation, TDH/MOF metadata availability, WPR profile support,
actual buffer/session behavior and decoded event coverage have not been tested
locally. They must pass in that single job, or stop with no tool installation,
privilege changes, fallback expansion or extra capture.

Official references: [WPR schema](https://learn.microsoft.com/en-us/windows-hardware/test/wpt/wprcontrolprofiles-schema),
[WPR commands](https://learn.microsoft.com/en-us/windows-hardware/test/wpt/wpr-command-line-options),
[ETW session bounds](https://learn.microsoft.com/en-us/windows/win32/api/evntrace/ns-evntrace-event_trace_properties),
[trace header and clock](https://learn.microsoft.com/en-us/windows/win32/api/evntrace/ns-evntrace-trace_logfile_header),
[ReadyThread](https://learn.microsoft.com/en-us/windows/win32/etw/readythread),
[file flush](https://learn.microsoft.com/en-us/windows/win32/etw/fileio-simpleop),
[file completion](https://learn.microsoft.com/en-us/windows/win32/etw/fileio-opend),
[physical disk flush](https://learn.microsoft.com/en-us/windows/win32/etw/diskio-typegroup3).
