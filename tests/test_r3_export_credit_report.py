"""Pure-data/std-lib report tests: no Qt, runtime imports, subprocess or load."""
import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "r3_export_credit_report.py"
SPEC = importlib.util.spec_from_file_location("credit_report_under_test", SCRIPT)
reporter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reporter)
SHA = "a" * 40


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def span(stage, **values):
    return {"stage": stage, "start_ns": 100, "end_ns": 200,
            "elapsed_ms": .0001, "thread_cpu_ns": 0, "outcome": "OK", **values}


def fixture(root, refused=False):
    repo, evidence = root / "repo", root / "evidence"
    repo.mkdir()
    evidence.mkdir()
    (repo / "fixture.py").write_text("# synthetic source\n", encoding="utf-8")
    hashes = {"fixture.py": hashlib.sha256((repo / "fixture.py").read_bytes()).hexdigest()}
    guard_hashes = {}
    for name in ("scripts/r3_hosted_credit_guard.py", "scripts/r3_export_credit_report.py", ".github/workflows/runtime-credit-diagnostics.yml"):
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# synthetic guard source\n", encoding="utf-8")
        guard_hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    identity = {"files": hashes, "digest": hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()}
    manifest = {"experiment": "export_credit_lifetime_abba_v2", "head": SHA, "dirty": "",
                "source_before": identity, "source_after": identity, "source_stable": True,
                "configuration": {"count": 96, "warmup": 8, "observer_order": list(reporter.ORDER), "qt_platform": "windows"},
                "measurement_status": "INVALID" if refused else "VALID", "performance_status": "NOT_ASSESSED",
                "identical_input_and_asset": not refused, "trials": []}
    for position, mode in enumerate(reporter.ORDER):
        arm = evidence / ("trial-" + str(position) + "-credit-" + mode)
        observed = {}
        consumers, ui = [], []
        for ordinal in range(1, 97):
            reject = refused and ordinal == 96
            source = {"state": "UNAVAILABLE" if reject else "AVAILABLE", "reason": "BUDGET_EXCEEDED" if reject else None,
                      "detail": "", "image_present": not reject}
            observed["result-" + str(ordinal)] = {"ordinal": ordinal, "status": "COMPLETE", "mode": "runtime",
                "source_outcomes": {"image": source, "count": {"state": "AVAILABLE", "reason": None, "detail": "", "image_present": False}}}
            consumers.append({"key": "result-" + str(ordinal), "ordinal": ordinal, "decoded": [] if reject else ["image"],
                              "failures": {}, "read_decode_ms": .1, "received_ns": 100, "decoded_ns": 200,
                              "model_ns": 200, "applied_to_live": True, "scope_ended_ns": 50,
                              "owner_age_at_send_ms": .00005, "received_to_model_ms": .0001})
            ui.append({"key": "result-" + str(ordinal), "ready_ns": 200, "gui_ns": 300, "scope_end_ns": 50, "paint_ns": 400})
        payload = {"arm": "none_qt", "qt_platform": "windows", "mode": "runtime", "job": "job-" + str(position),
                   "legacy_snapshot_policy": "NONE", "accepted_policy": "NONE", "capture_enabled": True,
                   "request": {"expected_runtime_instance_id": "runtime-" + str(position)},
                   "outcome_overflow": 0, "cleanup_errors": [], "record_retention": {"capacity_reached": False},
                   "input_sha256": "b" * 64, "observed_result_outcomes": observed,
                   "consumers": [{"raw_records": consumers, "rows": consumers[8:], "total_record_count": 96, "errors": [],
                                  "stats": {"received": 96, "decoded": 95 if refused else 96, "read_failed": 0},
                                  "unique_received": 88, "unique_decoded": 87 if refused else 88, "unique_applied": 88,
                                  "received_not_applied": 0, "missing_measured_ordinals": [], "missing_decoded_ordinals": [96] if refused else [],
                                  "missing_applied_ordinals": [], "unexpected_ordinals": [], "duplicate_ordinal_keys": {}}
                                 for _ in range(2)],
                   "ui": [{"raw_records": ui, "raw_rows": ui[8:], "total_record_count": 96,
                           "unique_committed": 88, "unique_painted": 88, "missing_committed_ordinals": [],
                           "missing_painted_ordinals": [], "unexpected_committed_ordinals": [], "unexpected_painted_ordinals": []}
                          for _ in range(2)],
                   "sampler": {"retired": True, "overflow": 0, "errors": [], "resource_coverage_complete": True,
                               "observed_roles": ["owner", "job", "exporter-0", "exporter-1"],
                               "requested": 3, "coalesced": 0, "capacity": 256, "samples": []},
                   "resource_samples": [{"time_ns": 123, "cache_bytes": 1024}], "ui_resources": [{"time_ns": 123, "windows": 2}]}
        def sample(roles, time):
            return {"requested_ns": time, "start_ns": time + 10, "end_ns": time + 100, "elapsed_ms": .00009,
                    "processes": {role: {"status": "OBSERVED", "pid": 10, "rss_bytes": 1000,
                                         "peak_rss_bytes": 1000, "handles": 2, "native_threads": 2, "cpu_seconds": .1} for role in roles},
                    "per_process_ms": {role: .00001 for role in roles},
                    "per_process_timestamps": {role: {"start_ns": time + 20, "end_ns": time + 30} for role in roles}}
        payload["owner_resources_before"] = sample(["owner"], 100)
        payload["owner_resources_after_cleanup"] = sample(["owner"], 500)
        payload["sampler"]["samples"] = [payload["owner_resources_before"],
            sample(["owner", "job", "exporter-0", "exporter-1"], 300), payload["owner_resources_after_cleanup"]]
        watch = {"status": "PASS", "exit_code": 0, "owner_retirement_verified": True, "timeout": False,
                 "log_overflow": False, "log_errors": [], "watchdog_seconds": 90}
        entry = {"position": position, "observer_mode": mode, "watchdog": watch, "trace_complete": True,
                 "trace_roles": {"execution": 1, "job": 1, "owner": 1, "exporter": 2}, "trace_dropped_rows": 0,
                 "passive_markers_disabled": True, "observer_configuration_matches": True,
                 "exact_ordinal_coverage": not refused, "input_sha256": "b" * 64,
                 "asset_fingerprint": {"complete": not refused, "calls": 190 if refused else 192, "distinct_fingerprints": 1,
                 "asset_bytes": None if refused else 123, "asset_sha256": None if refused else "c" * 64},
                 "split_accounting": {"status": "INVALID" if refused else "COMPLETE"}}
        execution_rows = [span("workflow.detect", ordinal=ordinal, invocation="invocation-" + str(ordinal),
                              start_ns=1000 * ordinal, end_ns=1000 * ordinal + 100) for ordinal in range(1, 97)]
        payload["raw_timings"] = [{"ordinal": row["ordinal"], "startNs": row["start_ns"], "endNs": row["end_ns"],
                                  "outcome": row["outcome"], "invocation": row["invocation"]} for row in execution_rows]
        payload.update(expected=88, executed=88, denominators={"planned_inputs": 96, "warmup_inputs": 8,
            "measured_inputs": 88, "executed_total": 96, "missing_execution_ordinals": [],
            "missing_measured_execution_ordinals": [], "duplicate_execution_ordinals": {},
            "out_of_range_execution_rows": [], "failed_execution_rows": []})
        job_rows = [span("job.worker_total")]
        owner_rows = [span("cleanup." + name) for name in ("qt", "client_0", "client_1", "control_channel", "server", "runtime", "post_cleanup_native_sample", "sampler")]
        export_rows = []
        for ordinal in range(1, 97):
            key = "result-" + str(ordinal)
            job_rows.extend(span(stage, ordinal=ordinal) for stage in ("scheduler.wait", "input.operator_total"))
            job_rows.append(span("capture.image_copy_descriptor", ordinal=ordinal, result_key=key, source="image", raw_bytes=1920 * 1080 * 3))
            if refused and ordinal == 96:
                continue
            for _ in range(2):
                owner_rows.extend(span(stage, ordinal=ordinal, result_key=key, source="image")
                                  for stage in ("client.asset_rpc", "client.png_decode"))
            export_rows.extend((span("export.png_encode", result_key=key, source="image", lane=0),
                                span("export.file_write", result_key=key, source="image", lane=0, bytes=123)))
        for number, (role, rows) in enumerate((("execution", execution_rows), ("job", job_rows), ("owner", owner_rows),
                                              ("exporter", export_rows), ("exporter", []))):
            write(arm / ("trace-" + role + "-" + str(number) + ".json"),
                  {"role": role, "pid": number + 1, "row_limit": 24000, "dropped_rows": 0, "rows": rows})
        split = {"role": "asset_split", "source_before": hashes, "source_after": hashes,
                 "source_complete": True, "source_unchanged": True, "instrumentation_disabled": False,
                 "diagnostic_errors": 0, "dropped_rows": 0, "outstanding_associations": {"requests": 0, "replies": 0, "tokens": 0},
                 "counters": {"client_calls_started": 190 if refused else 192, "client_calls_finished": 190 if refused else 192},
                 "rows": [{"stage": "client.asset_rpc_split", "asset_bytes": 123, "asset_sha256": "c" * 64}
                          for _ in range(190 if refused else 192)]}
        write(arm / "asset-split.json", split)
        write(arm / "owners-closed.json", {"all_original_trial_close_calls_returned": True})
        if mode == "on":
            parent_rows, capture_rows = [], []
            for ordinal in range(1, 97):
                reject = refused and ordinal == 96
                key = "result-" + str(ordinal)
                capture_rows.append(span("producer.capture_credit_acquire", runtime_id=payload["request"]["expected_runtime_instance_id"],
                    job_id=payload["job"], result_key=key, result_ordinal=ordinal, source_id="image", slot=ordinal % 2, lane=0,
                    capacity=8 * reporter.MIB, offset=0, raw_bytes=1920 * 1080 * 3, acquire_args=[False], acquire_kwargs={}, acquired=not reject))
                if reject:
                    continue
                identity_fields = {"pool_id": 1, "job_id": payload["job"], "result_key": key, "source_id": "image", "slot": ordinal % 2, "lane": 0, "thread_id": 12}
                for stage in reporter.REQUIRED:
                    row = span(stage, **identity_fields)
                    if stage == "parent.export_callback":
                        row["export_outcome"] = "AVAILABLE"
                    parent_rows.append(row)
                for stage, count in reporter.PHASES.items():
                    for _ in range(count):
                        row = span(stage, **identity_fields)
                        if stage.endswith((".read", ".hash_update")):
                            reading = stage.endswith(".read")
                            row.update(span_kind="first_to_last_call_envelope", aggregate={"calls": 2 if reading else 1,
                                "failures": 0, "wall_sum_ns": 0, "wall_max_ns": 0, "thread_cpu_sum_ns": 0,
                                "thread_cpu_max_ns": 0, "bytes": 123, "max_chunk_bytes": 123, "empty_calls": 1 if reading else 0})
                        parent_rows.append(row)
            common = {"source_before": hashes, "source_after": hashes, "source_complete": True, "source_unchanged": True,
                      "restored": True, "observer_retired": True, "dropped_rows": 0, "diagnostic_errors": 0, "instrumentation_disabled": False}
            accepted = 95 if refused else 96
            coverage = {"observed_exports": accepted, "admitted_exports": accepted, "available_callbacks": accepted,
                        "successful_adoptions": accepted, "incomplete_admitted_stages": {}, "missing_available_stages": {}, "missing_adopt_phases": {}}
            export = {**common, "role": "export_credit", "pending_instance_hooks": 0, "rows": parent_rows,
                      "counters": {"spans_completed": len(parent_rows)}, "stage_coverage": coverage}
            actual = {"attempts": 96, "accepted": accepted, "refused": int(refused), "failed": 0, "invalid_return": 0}
            capture = {**common, "role": "capture_credit", "pending_hooks": 0, "active_image_scopes": 0,
                       "rows": capture_rows, "counters": {**actual, "image_calls": 96}, "actual_attempts": actual,
                       "identity_counts": {"recorded_image_identities": 96, "recorded_results": 96, "recorded_sources": 1,
                                           "recorded_jobs": 1, "unattributed_attempts": 0}, "summary": {"enabled": True, "complete": True}}
            payload["export_credit_trace"] = {"enabled": True, "complete": True}
            payload["capture_credit_trace"] = {"enabled": True, "complete": True}
            write(arm / "export-credit.json", export)
            write(arm / "capture-credit-10.json", capture)
        write(arm / "trial.json", payload)
        manifest["trials"].append(entry)
    write(evidence / "evidence.json", manifest)
    write(evidence / "hosted-guard.json", {"schema_version": 1, "role": "hosted_credit_guard", "status": "COMPLETE",
        "source_before": guard_hashes, "source_after": guard_hashes, "source_stable": True,
        "setup_verified": True, "assignment_verified": True, "current_process_only": True, "handle_closed": True,
        "control_started": True, "kill_on_job_close": False, "errors": [], "configured_job_memory_bytes": 4 * 1024 ** 3,
        "readback_job_memory_bytes": 4 * 1024 ** 3, "final_job_memory_bytes": 4 * 1024 ** 3,
        "configured_limit_flags": 0x200, "readback_limit_flags": 0x200, "final_limit_flags": 0x200,
        "configured_priority_class": 0x4000, "readback_priority_class": 0x4000,
        "peak_job_memory_bytes": 500000, "peak_process_memory_bytes": 100000,
        "memory_limit_violation_status": "NOT_ASSESSED", "original_control_exit_code": int(refused), "wrapper_exit_code": int(refused)})
    return repo, evidence


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.repo, self.evidence = fixture(self.root)

    def tearDown(self):
        self.temporary.cleanup()

    def build(self, step="success"):
        return reporter.build_report(self.evidence, self.repo, SHA, step)

    def mutate(self, file, action):
        path = self.evidence / file
        value = json.loads(path.read_text())
        action(value)
        write(path, value)

    def on_mutate(self, file, action):
        self.mutate("trial-1-credit-on/" + file, action)

    def test_complete_keeps_every_ordinal_raw_parent_and_acquire_row(self):
        result = self.build()
        self.assertEqual(result["observation_status"], "COMPLETE")
        self.assertEqual(result["measurement_status"], "VALID")
        self.assertLess(len(reporter.encoded(result)), reporter.REPORT_BYTES)
        self.assertEqual(len(result["raw_files"]), 38)
        self.assertEqual(len(result["arms"][0]["sources"]["rows"]), 192)
        for arm in result["arms"]:
            for consumer in arm["consumers"]:
                self.assertEqual([row[1] for row in consumer["raw_records"]["rows"]], list(range(1, 97)))
        parent = result["arms"][1]["parent_rows"]
        self.assertEqual(sum(len(group["rows"]) for group in parent["groups"]), 96 * 19)
        self.assertEqual(len(result["arms"][1]["capture_rows"]["rows"]), 96)

    def test_refusal_is_complete_diagnostic_but_original_invalid_failure(self):
        other = self.root / "refusal"
        other.mkdir()
        repo, evidence = fixture(other, refused=True)
        result = reporter.build_report(evidence, repo, SHA, "failure")
        self.assertEqual(result["observation_status"], "COMPLETE")
        self.assertEqual(result["measurement_status"], "INVALID")
        self.assertEqual(result["original_step_outcome"], "failure")
        arm = result["arms"][1]
        self.assertEqual(arm["probe_coverage"]["refused"], 1)
        self.assertEqual(arm["probe_coverage"]["raw_export_identities"], 95)
        self.assertNotIn("result-96", [group["identity"][2] for group in arm["parent_rows"]["groups"]])
        self.assertIsNone(arm["original_control_fields"]["asset_fingerprint"]["asset_bytes"])
        self.assertEqual(arm["raw_asset_fingerprint"]["asset_bytes"], 123)

    def test_faults_never_become_complete(self):
        cases = [
            ("export-credit.json", lambda raw: raw["rows"].pop(), "parent_span_denominator"),
            ("capture-credit-10.json", lambda raw: raw["rows"][0].update(job_id="wrong"), "capture_identity_or_profile"),
            ("capture-credit-10.json", lambda raw: raw["rows"][0].update(lane=1), "capture_export_lane_join"),
            ("capture-credit-10.json", lambda raw: raw["rows"][0].update(end_ns=99), "capture_clock"),
            ("capture-credit-10.json", lambda raw: raw["rows"][0].update(thread_cpu_ns=-1), "capture_clock"),
            ("capture-credit-10.json", lambda raw: raw["rows"].clear(), "capture_denominator"),
            ("capture-credit-10.json", lambda raw: raw["counters"].update(attempts=95), "capture_counter_attempts"),
            ("export-credit.json", lambda raw: raw.update(observer_retired=False), "probe_observer_retired"),
            ("trial.json", lambda raw: raw.update(qt_platform="offscreen"), "native_windows_profile"),
        ]
        for name, action, issue in cases:
            with self.subTest(issue=issue):
                path = self.evidence / "trial-1-credit-on" / name
                original = path.read_bytes()
                self.on_mutate(name, action)
                result = self.build()
                self.assertEqual(result["observation_status"], "INVALID")
                self.assertIn(issue, result["arms"][1]["issues"])
                path.write_bytes(original)

    def test_fine_aggregate_clock_and_bytes(self):
        def fault(raw):
            row = next(row for row in raw["rows"] if row["stage"] == "parent.asset_adopt.read")
            row["aggregate"]["bytes"] += 1
            row["aggregate"]["wall_sum_ns"] = 999
        self.on_mutate("export-credit.json", fault)
        result = self.build()
        self.assertIn("fine_aggregate_clock_or_counters", result["arms"][1]["issues"])

    def test_source_changed_rejected(self):
        (self.repo / "fixture.py").write_text("changed")
        self.assertEqual(self.build()["observation_status"], "INVALID")

    def test_empty_raw_consumers_windows_and_sampler_cannot_inherit_valid_summary(self):
        def empty(raw):
            for consumer in raw["consumers"]:
                consumer.update(raw_records=[], total_record_count=0)
            for window in raw["ui"]:
                window.update(raw_records=[], total_record_count=0)
            raw["sampler"]["samples"] = [{"processes": {}}]
        self.mutate("trial-0-credit-off/trial.json", empty)
        result = self.build()
        self.assertEqual(result["observation_status"], "INVALID")
        issues = result["arms"][0]["issues"]
        for issue in ("consumer_raw_records_empty", "valid_consumer_formal_coverage", "window_raw_records_empty",
                      "valid_window_formal_coverage", "sampler_empty_process_sample", "sampler_raw_role_coverage"):
            self.assertIn(issue, issues)

    def test_sampler_claims_require_real_numeric_observed_rows(self):
        def invalid(raw):
            raw["sampler"]["samples"][1]["processes"]["job"].pop("rss_bytes")
        self.mutate("trial-0-credit-off/trial.json", invalid)
        result = self.build()
        self.assertEqual(result["observation_status"], "INVALID")
        self.assertIn("sampler_observed_metrics", result["arms"][0]["issues"])
        self.assertIn("sampler_raw_role_coverage", result["arms"][0]["issues"])

    def test_empty_base_traces_cannot_inherit_valid_manifest(self):
        for path in (self.evidence / "trial-0-credit-off").glob("trace-*.json"):
            raw = json.loads(path.read_text())
            raw["rows"] = []
            write(path, raw)
        result = self.build()
        self.assertEqual(result["observation_status"], "INVALID")
        issues = result["arms"][0]["issues"]
        for issue in ("raw_execution_missing", "raw_execution_timing_mismatch", "valid_raw_execution_coverage",
                      "raw_job_worker_missing", "raw_owner_cleanup_runtime", "raw_exporter_export.png_encode_denominator"):
            self.assertIn(issue, issues)

    def test_base_trace_summary_and_execution_ordinals_are_reconciled(self):
        self.mutate("evidence.json", lambda raw: raw["trials"][0].update(trace_roles={"execution": 5}, trace_dropped_rows=3))
        self.mutate("trial-0-credit-off/trace-execution-0.json", lambda raw: raw["rows"][0].update(ordinal=2))
        result = self.build()
        self.assertEqual(result["observation_status"], "INVALID")
        self.assertIn("base_trace_summary_mismatch", result["arms"][0]["issues"])
        self.assertIn("raw_execution_denominator_mismatch", result["arms"][0]["issues"])

    def test_raw_asset_change_cannot_inherit_identical_manifest(self):
        def change(raw):
            for row in raw["rows"]:
                row["asset_sha256"] = "d" * 64
        self.mutate("trial-0-credit-off/asset-split.json", change)
        result = self.build()
        self.assertEqual(result["observation_status"], "INVALID")
        self.assertIn("asset_fingerprint_summary_mismatch", result["arms"][0]["issues"])
        self.assertIn("raw_cross_arm_fingerprint_mismatch", result["issues"])

    def test_missing_formal_raw_records_with_stale_counters_are_invalid(self):
        def missing(raw):
            consumer = raw["consumers"][0]
            consumer["raw_records"].pop()
            consumer["rows"].pop()
            consumer["total_record_count"] -= 1
        self.mutate("trial-0-credit-off/trial.json", missing)
        result = self.build()
        self.assertIn("consumer_summary_missing_measured_ordinals", result["arms"][0]["issues"])
        self.assertIn("consumer_stats_decoded", result["arms"][0]["issues"])
        self.assertEqual(result["arms"][0]["consumers"][0]["raw_coverage"]["missing_measured_ordinals"], [96])

    def test_warmup_gaps_and_repeated_ui_updates_do_not_change_formal_gate(self):
        def change(raw):
            for consumer in raw["consumers"]:
                consumer["raw_records"] = consumer["raw_records"][8:]
                consumer["total_record_count"] = 88
                consumer["stats"].update(received=88, decoded=88)
            for window in raw["ui"]:
                window["raw_records"] = window["raw_records"][8:] + [window["raw_records"][-1]]
                window["raw_rows"] = window["raw_rows"] + [window["raw_rows"][-1]]
                window["total_record_count"] = 89
        self.mutate("trial-0-credit-off/trial.json", change)
        # Exercise only the original consumer/window formal gate here. A full
        # ABBA VALID control also independently requires 192 asset reads; its
        # raw read/decoder counters must remain consistent with warmup data.
        raw = json.loads((self.evidence / "trial-0-credit-off/trial.json").read_text())
        issues = reporter.Counter()
        consumers, windows = reporter.consumer_tables(raw, issues, original_valid=True)
        self.assertEqual(issues, {})
        self.assertEqual(consumers[0]["raw_coverage"]["missing_total_ordinals"], list(range(1, 9)))
        self.assertEqual(windows[0]["raw_coverage"]["formal_records"], 89)

    def test_truthful_invalid_window_gap_remains_explicit_complete_observation(self):
        def missing(raw):
            window = raw["ui"][0]
            window["raw_records"].pop()
            window["raw_rows"].pop()
            window.update(total_record_count=95, unique_committed=87, unique_painted=87,
                          missing_committed_ordinals=[96], missing_painted_ordinals=[96])
        self.mutate("trial-0-credit-off/trial.json", missing)
        def invalid_control(raw):
            raw["measurement_status"] = "INVALID"
            raw["trials"][0]["exact_ordinal_coverage"] = False
        self.mutate("evidence.json", invalid_control)
        self.mutate("hosted-guard.json", lambda raw: raw.update(original_control_exit_code=1, wrapper_exit_code=1))
        result = self.build(step="failure")
        self.assertEqual(result["observation_status"], "COMPLETE")
        self.assertEqual(result["measurement_status"], "INVALID")
        self.assertEqual(result["arms"][0]["windows"][0]["raw_coverage"]["missing_painted_ordinals"], [96])

    def test_absent_file_prints_small_complete_invalid_json(self):
        (self.evidence / "trial-1-credit-on/export-credit.json").unlink()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = reporter.main(["--evidence", str(self.evidence), "--repo", str(self.repo),
                                  "--expected-sha", SHA, "--original-step", "failure"])
        result = reporter.decode_report(output.getvalue())
        self.assertEqual(code, 1)
        self.assertTrue(result["report_rejected"])
        self.assertFalse(result["truncated"])
        self.assertEqual(result["original_step_outcome"], "failure")

    def test_raw_byte_and_aggregate_limits(self):
        reader = reporter.Reader(self.evidence)
        with self.assertRaisesRegex(reporter.InvalidEvidence, "oversized"):
            reader.read("evidence.json", 10)
        with patch.object(reporter, "INPUT_BYTES", 10):
            with self.assertRaisesRegex(reporter.InvalidEvidence, "aggregate_raw_byte_budget"):
                reader.read("evidence.json", 16 * reporter.MIB)

    def test_file_count_and_duplicate_json_rejected(self):
        reader = reporter.Reader(self.evidence)
        with patch.object(reporter, "INPUT_FILES", 0):
            with self.assertRaisesRegex(reporter.InvalidEvidence, "file_count"):
                reader.read("evidence.json", 16 * reporter.MIB)
        write(self.evidence / "duplicate.json", {})
        (self.evidence / "duplicate.json").write_text('{"key":1,"key":2}')
        with self.assertRaisesRegex(reporter.InvalidEvidence, "duplicate_json_key"):
            reader.read("duplicate.json", 1000)

    def test_path_traversal_and_symlink_escape_rejected(self):
        reader = reporter.Reader(self.evidence)
        for name in ("../repo/fixture.py", "/tmp/escape", "C:/escape", "..\\escape"):
            with self.subTest(name=name), self.assertRaises(reporter.InvalidEvidence):
                reader.read(name, 1000)
        link = self.evidence / "escape.json"
        try:
            link.symlink_to(self.repo / "fixture.py")
        except OSError:
            self.skipTest("symlink creation is unavailable")
        with self.assertRaisesRegex(reporter.InvalidEvidence, "linked"):
            reader.read("escape.json", 1000)

    def test_output_overflow_rejects_whole_report_without_truncation(self):
        result = self.build()
        output = io.StringIO()
        with patch.object(reporter, "REPORT_BYTES", 1024), patch.object(reporter, "build_report", return_value=result):
            with contextlib.redirect_stdout(output):
                code = reporter.main(["--evidence", str(self.evidence)])
        record = reporter.decode_report(output.getvalue())
        self.assertEqual(code, 1)
        self.assertEqual(record["error_code"], "complete_report_exceeds_stdout_budget")
        self.assertFalse(record["truncated"])
        self.assertLess(len(output.getvalue().encode()), 1024)

    def test_no_free_text_paths_or_image_payload_escape(self):
        secret = "C:\\private\\raw-image.png"
        self.on_mutate("trial.json", lambda raw: raw["observed_result_outcomes"]["result-1"]["source_outcomes"]["image"].update(detail=secret))
        self.assertNotIn(secret, reporter.encoded(self.build()).decode())
        self.on_mutate("export-credit.json", lambda raw: raw["rows"][0].update(image_bytes="forbidden"))
        with self.assertRaisesRegex(reporter.InvalidEvidence, "unknown_parent_row_field"):
            self.build()

    def test_workflow_keeps_original_failure_and_single_native_run(self):
        content = (SCRIPT.parents[1] / ".github/workflows/runtime-credit-diagnostics.yml").read_text()
        self.assertEqual(content.count("python scripts/r3_hosted_credit_guard.py"), 1)
        self.assertIn("QT_QPA_PLATFORM: windows", content)
        self.assertIn("if: always()", content)
        self.assertIn("persist-credentials: false", content)
        self.assertIn("timeout-minutes: 20", content)
        self.assertNotIn("continue-on-error", content)
        self.assertNotIn("upload-artifact", content)
        self.assertNotIn("pull_request", content)
        self.assertEqual(content.count("exit $LASTEXITCODE"), 2)

    def test_transport_round_trip_and_missing_part_integrity(self):
        result = self.build()
        output = reporter.transport(result)
        self.assertEqual(reporter.encoded(reporter.decode_report(output)), reporter.encoded(result))
        self.assertTrue(all(len(line.encode("ascii")) <= 16 * 1024 for line in output.splitlines()))
        self.assertLessEqual(len(output.encode("ascii")), reporter.REPORT_BYTES)
        lines = output.splitlines()
        del lines[2]
        with self.assertRaisesRegex(reporter.InvalidEvidence, "report_part_denominator"):
            reporter.decode_report("\n".join(lines) + "\n")
        lines = output.splitlines()
        lines[1] = lines[1].replace("schema_version", "schema_Version", 1)
        with self.assertRaisesRegex(reporter.InvalidEvidence, "report_content_integrity"):
            reporter.decode_report("\n".join(lines) + "\n")


if __name__ == "__main__":
    unittest.main()
