"""Read-only, stdlib-only, bounded hosted report for export-credit ABBA v2.

Never runs a workload or imports the runtime. COMPLETE describes observations,
not acceptance: the original control exit and INVALID measurement remain so.
All parent/capture rows and all consumer/UI raw records are retained in column
tables. Free-form exception/source detail text is represented by length/hash;
process logs, image bytes, filesystem paths and shared-memory names are absent.
"""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys


MIB = 1024 * 1024
REPORT_BYTES = MIB
CHUNK_BYTES = 16 * 1024 - 128
INPUT_BYTES = 256 * MIB
INPUT_FILES = 64
ORDER = ("off", "on", "on", "off")
COUNT = 96
IDENTITY = ("pool_id", "job_id", "result_key", "source_id", "slot", "lane")
PHASES = {"parent.asset_adopt." + name: (2 if name == "hash_hexdigest" else 1)
          for name in ("collect", "stat", "hash_create", "open", "stream_enter",
                       "read", "hash_update", "stream_exit", "replace", "hash_hexdigest")}
REQUIRED = ("parent.export_submit", "parent.task_dequeued", "parent.pipe_send",
            "parent.pipe_poll", "parent.pipe_recv", "parent.export_callback",
            "parent.credit_release", "parent.asset_adopt")
PARENT_FIELDS = ("stage", "start_ns", "end_ns", "elapsed_ms", "thread_cpu_ns", "thread_id",
                 "outcome", "export_outcome", "release_reason", "span_kind", "aggregate")
ACQUIRE_FIELDS = ("runtime_id", "job_id", "result_key", "result_ordinal", "source_id", "slot",
                  "lane", "capacity", "offset", "raw_bytes", "stage", "acquire_args",
                  "acquire_kwargs", "acquired", "start_ns", "end_ns", "elapsed_ms", "thread_cpu_ns", "outcome")
CONSUMER_FIELDS = ("key", "ordinal", "decoded", "failures", "read_decode_ms", "received_ns",
                   "decoded_ns", "model_ns", "applied_to_live", "scope_ended_ns",
                   "owner_age_at_send_ms", "received_to_model_ms")
UI_FIELDS = ("key", "ready_ns", "gui_ns", "scope_end_ns", "paint_ns")
AGG_FIELDS = ("calls", "failures", "wall_sum_ns", "wall_max_ns", "thread_cpu_sum_ns",
              "thread_cpu_max_ns", "bytes", "max_chunk_bytes", "empty_calls")
HASH = re.compile(r"[0-9a-f]{64}\Z")
SAFE_ID = re.compile(r"[A-Za-z0-9_.:-]{1,160}\Z")


class InvalidEvidence(ValueError):
    """Only a fixed diagnostic code, never raw file/exception content."""


def require(condition, code):
    if not condition:
        raise InvalidEvidence(code)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode("ascii")


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


def integer(value):
    return type(value) is int and 0 <= value <= (1 << 63) - 1


def pairs(items):
    result = {}
    for key, value in items:
        require(key not in result, "duplicate_json_key")
        result[key] = value
    return result


def text_digest(value):
    require(isinstance(value, str), "free_text_not_string")
    data = value.encode("utf-8")
    return {"utf8_bytes": len(data), "sha256": digest(data)}


def pick(value, fields):
    require(isinstance(value, dict), "expected_object")
    return {key: value[key] for key in fields if key in value}


class Reader:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.provenance = []
        self.total_bytes = 0

    def read(self, relative, limit, rows=None):
        require(isinstance(relative, str) and not Path(relative).is_absolute()
                and ".." not in relative.split("/") and "\\" not in relative and ":" not in relative,
                "raw_relative_path_schema")
        require(len(self.provenance) < INPUT_FILES, "raw_file_count_budget")
        path = self.root / relative
        require(path.is_file() and not path.is_symlink(), "missing_or_linked_raw_file")
        require(path.resolve().is_relative_to(self.root), "raw_file_outside_evidence")
        require(0 < path.stat().st_size <= limit, "empty_or_oversized_raw_file")
        require(self.total_bytes + path.stat().st_size <= INPUT_BYTES, "aggregate_raw_byte_budget")
        with path.open("rb") as stream:
            data = stream.read(limit + 1)
        require(0 < len(data) <= limit, "raw_file_changed_or_oversized")
        self.total_bytes += len(data)
        require(self.total_bytes <= INPUT_BYTES, "aggregate_raw_byte_budget")
        try:
            value = json.loads(data, object_pairs_hook=pairs,
                               parse_constant=lambda _: (_ for _ in ()).throw(InvalidEvidence("nonfinite_json")))
        except (ValueError, UnicodeError, RecursionError) as error:
            if isinstance(error, InvalidEvidence):
                raise
            raise InvalidEvidence("malformed_raw_json") from None
        require(isinstance(value, dict), "raw_root_not_object")
        record = {"file": relative, "bytes": len(data), "sha256": digest(data),
                  "fields": sorted(value)}
        if rows is not None:
            require(isinstance(value.get("rows"), list) and len(value["rows"]) <= rows,
                    "raw_rows_missing_or_oversized")
            require(all(isinstance(row, dict) for row in value["rows"]), "raw_row_not_object")
            record.update(row_count=len(value["rows"]),
                          row_fields=sorted({key for row in value["rows"] for key in row}))
        self.provenance.append(record)
        return value


def check(issues, condition, code):
    if not condition:
        issues[code] += 1


def clocks(row):
    return (all(integer(row.get(key)) for key in ("start_ns", "end_ns"))
            and row["end_ns"] >= row["start_ns"]
            and number(row.get("elapsed_ms"))
            and row["elapsed_ms"] == (row["end_ns"] - row["start_ns"]) / 1e6
            and (row.get("thread_cpu_ns") is None or integer(row["thread_cpu_ns"])))


def hash_map(value):
    require(isinstance(value, dict) and 0 < len(value) <= 4096, "source_hashes_missing_or_oversized")
    for name, sha in value.items():
        require(isinstance(name, str) and not Path(name).is_absolute()
                and "\\" not in name and ":" not in name and ".." not in name.split("/")
                and isinstance(sha, str) and HASH.fullmatch(sha), "source_hash_schema")
    return value


def source_pair(before, after, repo, issues):
    before, after = hash_map(before), hash_map(after)
    check(issues, before == after, "source_changed")
    source_bytes = 0
    for name, sha in after.items():
        path = repo / name
        inside = path.resolve().is_relative_to(repo)
        require(inside and not path.is_symlink(), "source_path_escape")
        source_bytes += path.stat().st_size if path.is_file() else 0
        require(source_bytes <= 64 * MIB, "source_identity_byte_budget")
        check(issues, path.is_file() and path.stat().st_size <= 24 * MIB
              and digest(path.read_bytes()) == sha, "source_checkout_mismatch")
    # Exact before map plus a complete after delta avoids repeating hundreds of hashes.
    return {"before": before, "after_changes": {key: value for key, value in after.items()
            if before.get(key) != value}, "after_removed": sorted(set(before) - set(after)),
            "equal": before == after}


def table(rows, fields, maximum):
    require(isinstance(rows, list) and len(rows) <= maximum, "table_row_bound")
    require(all(isinstance(row, dict) and set(row) <= set(fields) for row in rows), "unknown_table_field")
    return {"columns": list(fields), "rows": [[row.get(key) for key in fields] for row in rows]}


def parent_table(rows):
    groups = {}
    stages = sorted({row.get("stage") for row in rows})
    threads = sorted({row.get("thread_id") for row in rows})
    require(all(integer(value) for value in threads), "parent_thread_schema")
    require(all(isinstance(stage, str) and SAFE_ID.fullmatch(stage) for stage in stages), "parent_stage_schema")
    allowed = set(IDENTITY + PARENT_FIELDS)
    for index, row in enumerate(rows):
        require(set(row) <= allowed, "unknown_parent_row_field")
        key = tuple(row.get(name) for name in IDENTITY)
        group = groups.setdefault(key, [])
        values = [row.get(name) for name in PARENT_FIELDS]
        values[0] = stages.index(row["stage"])
        values[5] = threads.index(row["thread_id"])
        if values[-1] is not None:
            require(isinstance(values[-1], dict) and set(values[-1]) == set(AGG_FIELDS), "aggregate_field_schema")
            values[-1] = [values[-1][name] for name in AGG_FIELDS]
        group.append([index, *values])
    return {"identity_columns": list(IDENTITY), "row_columns": ["raw_row_index", *PARENT_FIELDS],
            "stage_dictionary": stages, "thread_id_dictionary": threads, "aggregate_columns": list(AGG_FIELDS),
            "groups": [{"identity": list(key), "rows": value} for key, value in groups.items()]}


def observer_metadata(raw, repo, issues, capture=False):
    check(issues, raw.get("source_complete") is True and raw.get("source_unchanged") is True,
          "probe_source_incomplete")
    source = source_pair(raw.get("source_before"), raw.get("source_after"), repo, issues)
    for field in ("restored", "observer_retired"):
        check(issues, raw.get(field) is True, "probe_" + field)
    for field in ("dropped_rows", "diagnostic_errors", "pending_hooks" if capture else "pending_instance_hooks"):
        check(issues, raw.get(field) == 0, "probe_" + field)
    check(issues, raw.get("instrumentation_disabled") is False, "probe_disabled")
    counters = raw.get("counters")
    require(isinstance(counters, dict) and all(integer(value) for value in counters.values()), "counter_schema")
    for key, value in counters.items():
        if any(part in key for part in ("invalid", "unattributed", "overflow", "dropped", "conflict", "unverified", "active_at_exit")):
            check(issues, value == 0, "counter_" + key)
    result = pick(raw, ("role", "schema_version", "pid", "row_limit", "hook_limit", "pool_limit",
                       "trace_bytes_limit", "dropped_rows", "diagnostic_errors", "restored", "observer_retired",
                       "pending_hooks", "pending_instance_hooks", "active_image_scopes", "instrumentation_disabled",
                       "actual_attempts", "identity_counts", "stage_coverage", "adopt_phase_schema",
                       "source_complete", "source_unchanged", "clock", "performance_verdict"))
    result.update(counters=counters, sources=source,
                  observer_cost=pick(raw.get("observer_cost", {}), ("calls", "wall_ns", "thread_cpu_ns", "max_wall_ns", "max_thread_cpu_ns")))
    return result


def fine_phases(rows, encoded_bytes, issues):
    groups = defaultdict(list)
    for row in rows:
        if row.get("outcome") == "OK":
            groups[row.get("stage")].append(row)
    for stage, count in {**{key: 1 for key in REQUIRED}, **PHASES}.items():
        check(issues, len(groups[stage]) == count, "admitted_" + stage + "_count")
    owners = groups["parent.asset_adopt"]
    if len(owners) != 1:
        return
    owner = owners[0]
    if not clocks(owner):
        return
    for stage in PHASES:
        for row in groups[stage]:
            check(issues, clocks(row) and owner["start_ns"] <= row["start_ns"] <= row["end_ns"] <= owner["end_ns"],
                  "fine_phase_clock_or_envelope")
    aggregates = {}
    for stage in ("parent.asset_adopt.read", "parent.asset_adopt.hash_update"):
        if len(groups[stage]) != 1:
            continue
        row = groups[stage][0]
        aggregate = row.get("aggregate")
        require(isinstance(aggregate, dict) and set(aggregate) == set(AGG_FIELDS), "aggregate_schema")
        valid = (all(integer(aggregate[key]) for key in AGG_FIELDS if not key.startswith("thread_cpu"))
                 and aggregate["calls"] >= 1 and aggregate["failures"] == 0 and clocks(row)
                 and aggregate["wall_max_ns"] <= aggregate["wall_sum_ns"] <= row["end_ns"] - row["start_ns"]
                 and aggregate["wall_sum_ns"] <= aggregate["calls"] * aggregate["wall_max_ns"]
                 and row.get("span_kind") == "first_to_last_call_envelope")
        cpu_sum, cpu_max, cpu = aggregate["thread_cpu_sum_ns"], aggregate["thread_cpu_max_ns"], row.get("thread_cpu_ns")
        valid = valid and ((cpu_sum is None and cpu_max is None and cpu is None)
                          or (all(integer(value) for value in (cpu_sum, cpu_max, cpu))
                              and cpu_max <= cpu_sum <= cpu and cpu_sum <= aggregate["calls"] * cpu_max))
        check(issues, valid, "fine_aggregate_clock_or_counters")
        if valid:
            aggregates[stage] = aggregate
    read, update = (aggregates.get("parent.asset_adopt." + stage) for stage in ("read", "hash_update"))
    if read is None or update is None:
        check(issues, False, "fine_aggregate_missing")
    else:
        check(issues, 0 < read["bytes"] == update["bytes"] == encoded_bytes <= 8 * MIB
              and update["calls"] == (read["bytes"] + 65535) // 65536
              and read["calls"] == update["calls"] + 1 and read["empty_calls"] == 1
              and update["empty_calls"] == 0 and read["max_chunk_bytes"] == min(read["bytes"], 65536)
              and update["max_chunk_bytes"] == read["max_chunk_bytes"], "fine_read_update_denominator")


def probes(payload, capture, export, encoded_bytes, issues):
    expected = payload["observed_result_outcomes"]
    attempts, exports, groups = {}, {}, defaultdict(list)
    accepted = refused = 0
    for row in capture["rows"]:
        require(set(row) == set(ACQUIRE_FIELDS), "capture_row_schema")
        key = row.get("result_key")
        valid = (key in expected and row["result_ordinal"] == expected[key]["ordinal"]
                 and row["job_id"] == payload["job"] and row["source_id"] == "image"
                 and row["runtime_id"] == payload["request"]["expected_runtime_instance_id"]
                 and type(row["slot"]) is int and row["slot"] in (0, 1)
                 and type(row["lane"]) is int and row["lane"] == 0
                 and type(row["offset"]) is int and row["offset"] == 0
                 and row["capacity"] == 8 * MIB and row["raw_bytes"] == 1920 * 1080 * 3
                 and row["stage"] == "producer.capture_credit_acquire")
        check(issues, valid, "capture_identity_or_profile")
        check(issues, clocks(row), "capture_clock")
        check(issues, row["acquire_args"] == [False] and row["acquire_args"][0] is False
              and row["acquire_kwargs"] == {} and row["outcome"] == "OK"
              and type(row["acquired"]) is bool, "capture_original_call")
        check(issues, key not in attempts, "duplicate_capture")
        attempts[key] = row
        accepted += row["acquired"] is True
        refused += row["acquired"] is False
        if valid and row["acquired"] is False:
            image = expected[key]["source_outcomes"]["image"]
            check(issues, image["state"] == "UNAVAILABLE" and image.get("reason") == "BUDGET_EXCEEDED",
                  "capture_refusal_source_mismatch")
    check(issues, len(capture["rows"]) == COUNT and set(attempts) == set(expected), "capture_denominator")
    actual = {"attempts": len(capture["rows"]), "accepted": accepted, "refused": refused,
              "failed": sum(row["outcome"] != "OK" for row in capture["rows"]),
              "invalid_return": sum(row["outcome"] == "OK" and type(row["acquired"]) is not bool for row in capture["rows"])}
    check(issues, capture.get("actual_attempts") == actual, "capture_actual_counters")
    for key, value in actual.items():
        check(issues, capture["counters"].get(key, 0) == value, "capture_counter_" + key)
    check(issues, capture["counters"].get("image_calls") == COUNT and capture.get("active_image_scopes") == 0,
          "capture_image_scope_denominator")
    identities = {"recorded_image_identities": len(attempts), "recorded_results": len(attempts),
                  "recorded_sources": 1, "recorded_jobs": 1, "unattributed_attempts": 0}
    check(issues, capture.get("identity_counts") == identities, "capture_identity_counters")
    for row in export["rows"]:
        check(issues, clocks(row), "parent_clock")
        if "result_key" not in row:
            check(issues, row.get("stage") == "parent.credit_release_unattributed"
                  and row.get("release_reason") == "outside_dequeued_export" and row.get("outcome") == "OK"
                  and type(row.get("pool_id")) is int and 1 <= row["pool_id"] <= 4
                  and type(row.get("slot")) is int and row["slot"] in (0, 1)
                  and type(row.get("lane")) is int and row["lane"] in (0, 1),
                  "unexpected_unattributed_parent_row")
            continue
        key = row["result_key"]
        valid = (key in expected and row.get("job_id") == payload["job"] and row.get("source_id") == "image"
                 and type(row.get("pool_id")) is int and 1 <= row["pool_id"] <= 4
                 and type(row.get("slot")) is int and row["slot"] in (0, 1)
                 and type(row.get("lane")) is int and row["lane"] == 0)
        check(issues, valid, "parent_identity")
        group_key = tuple(row.get(name) for name in IDENTITY)
        groups[group_key].append(row)
        if row.get("stage") == "parent.export_submit" and row.get("outcome") == "OK":
            check(issues, key not in exports, "duplicate_parent_export")
            exports[key] = row
    check(issues, export["counters"].get("spans_completed") == len(export["rows"]), "parent_span_denominator")
    for key, rows in groups.items():
        fine_phases(rows, encoded_bytes, issues)
        check(issues, all(row.get("outcome") == "OK" for row in rows), "failed_parent_phase")
        check(issues, all(row.get("export_outcome") == "AVAILABLE" for row in rows
                          if row.get("stage") == "parent.export_callback"), "failed_parent_callback")
    check(issues, len(groups) == len(exports), "parent_group_denominator")
    accepted_keys = {key for key, row in attempts.items() if row["acquired"] is True}
    check(issues, set(exports) == accepted_keys, "capture_export_identity_join")
    for key in set(exports) & accepted_keys:
        check(issues, all(exports[key].get(field) == attempts[key].get(field)
                          for field in ("job_id", "result_key", "source_id", "slot", "lane")), "capture_export_lane_join")
        check(issues, expected[key]["source_outcomes"]["image"].get("state") == "AVAILABLE",
              "admitted_source_not_available")
    coverage = export.get("stage_coverage", {})
    for key in ("observed_exports", "admitted_exports", "available_callbacks", "successful_adoptions"):
        check(issues, coverage.get(key) == len(exports), "parent_coverage_" + key)
    for key in ("incomplete_admitted_stages", "missing_available_stages", "missing_adopt_phases"):
        check(issues, coverage.get(key) == {}, "parent_coverage_" + key)
    return {"expected_attempts": COUNT, "raw_attempts": len(capture["rows"]), "accepted": accepted,
            "refused": refused, "raw_export_identities": len(exports),
            "strict_full_image_coverage": len(exports) == COUNT and refused == 0}


def numeric_summary(rows):
    require(isinstance(rows, list) and len(rows) <= 6000, "resource_rows_bound")
    fields = sorted({key for row in rows for key, value in row.items() if number(value)})
    return {"samples": len(rows), "fields": {key: {"observed": len(values), "min": min(values),
            "max": max(values), "first": values[0], "last": values[-1]}
            for key in fields if (values := [row[key] for row in rows if number(row.get(key))])}}


def resource_summary(payload, issues):
    sampler = payload["sampler"]
    check(issues, sampler.get("retired") is True and sampler.get("overflow") == 0
          and sampler.get("errors") == [] and sampler.get("resource_coverage_complete") is True,
          "sampler_incomplete")
    check(issues, set(sampler.get("observed_roles", [])) >= {"owner", "job", "exporter-0", "exporter-1"},
          "sampler_roles")
    samples = sampler.get("samples")
    require(isinstance(samples, list) and 0 < len(samples) <= 256, "sampler_rows_bound")
    by_role = defaultdict(list)
    statuses = defaultdict(Counter)
    requested_roles, observed_roles = set(), set()
    for row in samples:
        require(isinstance(row, dict) and isinstance(row.get("processes"), dict), "sampler_sample_schema")
        process_rows = row["processes"]
        check(issues, bool(process_rows), "sampler_empty_process_sample")
        check(issues, all(integer(row.get(key)) for key in ("requested_ns", "start_ns", "end_ns"))
              and row["requested_ns"] <= row["start_ns"] <= row["end_ns"]
              and number(row.get("elapsed_ms")) and row["elapsed_ms"] == (row["end_ns"] - row["start_ns"]) / 1e6,
              "sampler_sample_clock")
        boundaries, timings = row.get("per_process_timestamps", {}), row.get("per_process_ms", {})
        require(isinstance(boundaries, dict) and isinstance(timings, dict), "sampler_process_clock_schema")
        check(issues, set(boundaries) == set(timings) == set(process_rows), "sampler_process_clock_denominator")
        for role, value in process_rows.items():
            require(role in ("owner", "job", "exporter-0", "exporter-1"), "sampler_role_schema")
            require(isinstance(value, dict), "sampler_metric_schema")
            requested_roles.add(role)
            statuses[role][value.get("status")] += 1
            boundary = boundaries.get(role, {})
            check(issues, all(integer(boundary.get(key)) for key in ("start_ns", "end_ns"))
                  and integer(row.get("start_ns")) and integer(row.get("end_ns"))
                  and row["start_ns"] <= boundary["start_ns"] <= boundary["end_ns"] <= row["end_ns"]
                  and number(timings.get(role)) and timings[role] == (boundary["end_ns"] - boundary["start_ns"]) / 1e6,
                  "sampler_process_clock")
            if value.get("status") == "OBSERVED":
                valid = (all(integer(value.get(key)) for key in ("pid", "rss_bytes", "peak_rss_bytes", "handles", "native_threads"))
                         and value["pid"] > 0 and value["native_threads"] > 0
                         and value["peak_rss_bytes"] >= value["rss_bytes"]
                         and number(value.get("cpu_seconds")) and value["cpu_seconds"] >= 0
                         and ("python_threads" not in value or integer(value["python_threads"])))
                check(issues, valid, "sampler_observed_metrics")
                if valid:
                    observed_roles.add(role)
                by_role[role].append(value)
    check(issues, set(sampler.get("observed_roles", [])) == observed_roles, "sampler_claimed_roles_mismatch")
    check(issues, observed_roles >= {"owner", "job", "exporter-0", "exporter-1"}
          and observed_roles == requested_roles, "sampler_raw_role_coverage")
    check(issues, all(integer(sampler.get(key)) for key in ("requested", "coalesced", "overflow", "capacity"))
          and sampler["requested"] == len(samples) + sampler["coalesced"] + sampler["overflow"]
          and len(samples) <= sampler["capacity"] <= 256, "sampler_sample_denominator")
    owners = {}
    for name in ("owner_resources_before", "owner_resources_after_cleanup"):
        value = payload.get(name, {})
        owner = value.get("processes", {}).get("owner", {})
        check(issues, value in samples and set(value.get("processes", {})) == {"owner"}
              and owner.get("status") == "OBSERVED", "sampler_owner_boundary_missing")
        owners[name] = {"timestamps": pick(value, ("requested_ns", "start_ns", "end_ns", "elapsed_ms")),
                        "owner": pick(value.get("processes", {}).get("owner", {}),
                        ("pid", "status", "rss_bytes", "peak_rss_bytes", "handles", "native_threads", "cpu_seconds", "python_threads"))}
    return {**owners, "sampler": pick(sampler, ("observed_roles", "resource_coverage_complete", "requested", "coalesced", "overflow", "capacity", "retired")),
            "raw_role_coverage": {"requested_roles": sorted(requested_roles), "valid_observed_roles": sorted(observed_roles), "samples": len(samples)},
            "native_by_role": {role: {**numeric_summary(rows), "statuses": dict(statuses[role])}
                               for role, rows in by_role.items()},
            "presentation": numeric_summary(payload["resource_samples"]),
            "ui": numeric_summary(payload["ui_resources"])}


def outcomes(payload, issues):
    observed = payload.get("observed_result_outcomes")
    require(isinstance(observed, dict) and len(observed) <= 128, "source_rows_bound")
    counts = Counter(value.get("ordinal") for value in observed.values())
    check(issues, len(observed) == COUNT and counts == Counter(range(1, COUNT + 1)), "result_ordinal_denominator")
    rows, details = [], []
    for key, value in observed.items():
        require(isinstance(key, str) and SAFE_ID.fullmatch(key), "result_identity_schema")
        require(set(value) == {"ordinal", "status", "mode", "source_outcomes"}, "source_record_schema")
        require(set(value["source_outcomes"]) == {"image", "count"}, "source_denominator")
        for source_id, source in value["source_outcomes"].items():
            require(source_id in ("image", "count"), "source_id_schema")
            require(set(source) == {"state", "reason", "detail", "image_present"}, "source_outcome_schema")
            detail = text_digest(source["detail"])
            if detail not in details:
                details.append(detail)
            rows.append([key, value["ordinal"], value["status"], value["mode"], source_id,
                         source["state"], source["reason"], source["image_present"], details.index(detail)])
    return {"columns": ["result_key", "ordinal", "status", "mode", "source_id", "state", "reason", "image_present", "detail_digest_index"],
            "detail_digest_dictionary": details, "rows": rows}


def consumer_tables(payload, issues, original_valid=False):
    require(isinstance(payload.get("consumers"), list) and len(payload["consumers"]) == 2,
            "consumer_count")
    require(isinstance(payload.get("ui"), list) and len(payload["ui"]) == 2, "window_count")
    observed = payload["observed_result_outcomes"]
    consumers, windows = [], []
    formal = set(range(9, COUNT + 1))
    all_ordinals = set(range(1, COUNT + 1))
    raw_key_ordinals = {}
    for consumer in payload["consumers"]:
        raw = consumer.get("raw_records")
        require(isinstance(raw, list) and len(raw) <= 128, "consumer_raw_records_missing_or_oversized")
        check(issues, bool(raw), "consumer_raw_records_empty")
        measured = [row for row in raw if type(row.get("ordinal")) is int and row["ordinal"] > 8]
        check(issues, consumer.get("rows") == measured, "consumer_measured_subset_mismatch")
        seen = {row.get("ordinal") for row in measured}
        decoded = {row["ordinal"] for row in measured if "image" in row.get("decoded", []) and not row.get("failures")}
        applied = {row["ordinal"] for row in measured if row.get("applied_to_live") and row.get("model_ns")}
        keys_by_ordinal = defaultdict(set)
        for row in measured:
            keys_by_ordinal[row["ordinal"]].add(row["key"])
        reconstructed = {"missing_measured_ordinals": sorted(formal - seen),
            "missing_decoded_ordinals": sorted(formal - decoded), "missing_applied_ordinals": sorted(formal - applied),
            "unexpected_ordinals": sorted(seen - formal),
            "duplicate_ordinal_keys": {str(key): sorted(keys) for key, keys in keys_by_ordinal.items() if len(keys) > 1},
            "received_not_applied": sum(not row.get("applied_to_live") for row in measured),
            "unique_received": len({row["key"] for row in measured}),
            "unique_decoded": len({row["key"] for row in measured if "image" in row.get("decoded", []) and not row.get("failures")}),
            "unique_applied": len({row["key"] for row in measured if row.get("applied_to_live")})}
        for name, actual in reconstructed.items():
            check(issues, consumer.get(name) == actual, "consumer_summary_" + name)
        stats = consumer.get("stats", {})
        require(isinstance(stats, dict) and all(integer(value) for value in stats.values()), "consumer_stats_schema")
        # Repeated records may reuse an already decoded image/failure. Bound
        # original counters between distinct result/source pairs and raw rows,
        # rather than assuming one decode per GUI update or per duplicate row.
        for field, predicate in (("decoded", lambda row: "image" in row.get("decoded", [])),
                                 ("read_failed", lambda row: "image" in row.get("failures", {}))):
            matching = [row for row in raw if predicate(row)]
            check(issues, integer(stats.get(field))
                  and len({row["key"] for row in matching}) <= stats[field] <= len(matching), "consumer_stats_" + field)
        check(issues, integer(stats.get("received")) and stats["received"] >= len({row["key"] for row in raw}),
              "consumer_stats_received")
        if original_valid:
            check(issues, seen == decoded == applied == formal and not reconstructed["duplicate_ordinal_keys"],
                  "valid_consumer_formal_coverage")
        copied = []
        for row in raw:
            require(set(row) == set(CONSUMER_FIELDS), "consumer_row_schema")
            check(issues, row["key"] in observed and row["ordinal"] == observed[row["key"]]["ordinal"], "consumer_result_join")
            raw_key_ordinals[row["key"]] = row["ordinal"]
            for name in ("received_ns", "decoded_ns", "scope_ended_ns"):
                check(issues, integer(row[name]), "consumer_clock")
            check(issues, row["model_ns"] is None or integer(row["model_ns"]), "consumer_model_clock")
            check(issues, integer(row["received_ns"]) and integer(row["decoded_ns"])
                  and row["received_ns"] <= row["decoded_ns"]
                  and (row["model_ns"] is None or row["model_ns"] == row["decoded_ns"]), "consumer_clock_order")
            failures = row["failures"]
            require(isinstance(failures, dict) and set(failures) <= {"image", "count"}, "consumer_failure_schema")
            copied.append({**row, "failures": {key: text_digest(value) for key, value in failures.items()}})
        consumers.append({"raw_records": table(copied, CONSUMER_FIELDS, 128),
                          "raw_coverage": {"total_records": len(raw), "formal_records": len(measured),
                            "missing_total_ordinals": sorted(all_ordinals - {row["ordinal"] for row in raw}),
                            **reconstructed},
                          "summary": pick(consumer, ("total_record_count", "stats", "unique_received", "unique_decoded", "unique_applied", "p95_model_ms",
                            "missing_measured_ordinals", "missing_decoded_ordinals", "missing_applied_ordinals", "unexpected_ordinals", "duplicate_ordinal_keys", "received_not_applied")),
                          "error_text_digests": [text_digest(value) for value in consumer.get("errors", [])]})
        check(issues, consumer.get("total_record_count") == len(raw), "consumer_retention_count")
    for window in payload["ui"]:
        raw = window.get("raw_records")
        require(isinstance(raw, list), "window_raw_records_missing")
        check(issues, bool(raw), "window_raw_records_empty")
        measured = [row for row in raw if row.get("key") not in raw_key_ordinals or raw_key_ordinals[row["key"]] > 8]
        check(issues, window.get("raw_rows") == measured, "window_measured_subset_mismatch")
        formal_key_ordinals = {key: ordinal for key, ordinal in raw_key_ordinals.items() if ordinal > 8}
        committed = {formal_key_ordinals.get(row.get("key")) for row in measured}
        painted = {formal_key_ordinals.get(row.get("key")) for row in measured if row.get("paint_ns")}
        reconstructed = {"missing_committed_ordinals": sorted(formal - committed),
            "missing_painted_ordinals": sorted(formal - painted),
            "unexpected_committed_ordinals": sorted(str(value) for value in committed - formal),
            "unexpected_painted_ordinals": sorted(str(value) for value in painted - formal),
            "unique_committed": len({row.get("key") for row in measured}),
            "unique_painted": len({row.get("key") for row in measured if row.get("paint_ns")})}
        for name, actual in reconstructed.items():
            check(issues, window.get(name) == actual, "window_summary_" + name)
        if original_valid:
            check(issues, committed == painted == formal, "valid_window_formal_coverage")
        for row in raw:
            check(issues, row.get("key") in observed, "window_result_join")
            check(issues, all(integer(row.get(key)) for key in ("ready_ns", "gui_ns", "scope_end_ns"))
                  and row["ready_ns"] <= row["gui_ns"]
                  and (row.get("paint_ns") is None or integer(row["paint_ns"]) and row["paint_ns"] >= row["gui_ns"]),
                  "window_clock")
        windows.append({"raw_records": table(raw, UI_FIELDS, 256),
                        "raw_coverage": {"total_records": len(raw), "formal_records": len(measured),
                            "missing_total_ordinals": sorted(all_ordinals - {raw_key_ordinals.get(row.get("key")) for row in raw}),
                            **reconstructed},
                        "summary": pick(window, ("total_record_count", "unique_committed", "unique_painted", "p95_scope_to_gui_ms", "p95_ready_to_gui_ms", "p95_scope_to_paint_ms",
                          "missing_committed_ordinals", "missing_painted_ordinals", "unexpected_committed_ordinals", "unexpected_painted_ordinals"))})
        check(issues, window.get("total_record_count") == len(raw), "window_retention_count")
    return consumers, windows


def base_trace_coverage(payload, traces, entry, rpc_count, asset_bytes, issues, original_valid):
    """Rebuild fixed-workload denominators; idle exporter files may be empty."""
    by_role = defaultdict(list)
    for raw in traces:
        by_role[raw["role"]].extend(raw["rows"])
    roles = dict(Counter(raw["role"] for raw in traces))
    dropped = sum(raw["dropped_rows"] for raw in traces)
    check(issues, entry.get("trace_roles") == roles and entry.get("trace_dropped_rows") == dropped,
          "base_trace_summary_mismatch")
    detect = [row for row in by_role["execution"] if row.get("stage") == "workflow.detect"]
    timings = sorted([{"ordinal": row.get("ordinal"), "startNs": row["start_ns"], "endNs": row["end_ns"],
                       "outcome": row["outcome"], "invocation": row.get("invocation")} for row in detect],
                     key=lambda row: row["startNs"])
    check(issues, payload.get("raw_timings") == timings, "raw_execution_timing_mismatch")
    seen = Counter(row["ordinal"] for row in timings)
    all_ordinals, formal = set(range(1, COUNT + 1)), set(range(9, COUNT + 1))
    completed = {row["ordinal"] for row in timings if row["ordinal"] in formal and row["outcome"] == "OK"}
    denominator = {"planned_inputs": COUNT, "warmup_inputs": 8, "measured_inputs": 88,
        "executed_total": len(timings), "missing_execution_ordinals": sorted(all_ordinals - set(seen)),
        "missing_measured_execution_ordinals": sorted(formal - completed),
        "duplicate_execution_ordinals": {str(key): value for key, value in seen.items() if value > 1},
        "out_of_range_execution_rows": [row for row in timings if row["ordinal"] not in all_ordinals],
        "failed_execution_rows": [row for row in timings if row["outcome"] != "OK"]}
    check(issues, payload.get("denominators") == denominator and payload.get("executed") == len(completed)
          and payload.get("expected") == 88, "raw_execution_denominator_mismatch")
    check(issues, bool(timings), "raw_execution_missing")
    if original_valid:
        check(issues, seen == Counter(range(1, COUNT + 1)) and not denominator["failed_execution_rows"],
              "valid_raw_execution_coverage")
    stages = {role: Counter(row.get("stage") for row in rows) for role, rows in by_role.items()}
    job_rows = by_role["job"]
    worker = [row for row in job_rows if row.get("stage") == "job.worker_total"]
    check(issues, len(worker) == 1 and worker[0].get("outcome") == "OK", "raw_job_worker_missing")
    for stage in ("scheduler.wait", "input.operator_total"):
        rows = [row for row in job_rows if row.get("stage") == stage]
        check(issues, Counter(row.get("ordinal") for row in rows) == seen, "raw_job_" + stage + "_denominator")
    expected = payload["observed_result_outcomes"]
    capture_rows = [row for row in job_rows if row.get("stage") == "capture.image_copy_descriptor"]
    check(issues, Counter(row.get("result_key") for row in capture_rows) == Counter(expected.keys())
          and all(row.get("source") == "image" and row.get("ordinal") == expected[row["result_key"]]["ordinal"]
                  and row.get("raw_bytes") == 1920 * 1080 * 3 for row in capture_rows), "raw_job_capture_denominator")
    owner_rows = by_role["owner"]
    for suffix in ("qt", "client_0", "client_1", "control_channel", "server", "runtime", "post_cleanup_native_sample", "sampler"):
        rows = [row for row in owner_rows if row.get("stage") == "cleanup." + suffix]
        check(issues, len(rows) == 1 and rows[0].get("outcome") == "OK", "raw_owner_cleanup_" + suffix)
    reads = [row for row in owner_rows if row.get("stage") == "client.asset_rpc"]
    decodes = [row for row in owner_rows if row.get("stage") == "client.png_decode" and row.get("outcome") == "OK"]
    expected_decodes = sum(consumer["stats"]["decoded"] for consumer in payload["consumers"])
    check(issues, len(reads) == rpc_count and len(decodes) == expected_decodes, "raw_owner_read_decode_denominator")
    for row in reads + decodes:
        key = row.get("result_key")
        check(issues, key in expected and row.get("source") == "image"
              and row.get("ordinal") == expected[key]["ordinal"], "raw_owner_read_identity")
    available = {key for key, value in expected.items() if value["source_outcomes"]["image"].get("state") == "AVAILABLE"}
    for stage in ("export.png_encode", "export.file_write"):
        rows = [row for row in by_role["exporter"] if row.get("stage") == stage and row.get("outcome") == "OK"]
        check(issues, Counter(row.get("result_key") for row in rows) == Counter(available), "raw_exporter_" + stage + "_denominator")
        check(issues, all(row.get("source") == "image" and row.get("lane") == 0
              and (stage != "export.file_write" or row.get("bytes") == asset_bytes) for row in rows), "raw_exporter_profile")
    return {"stage_counts": {role: dict(counts) for role, counts in stages.items()},
            "execution_denominators": denominator, "expected_read_attempts": rpc_count,
            "successful_decode_rows": len(decodes), "available_source_exports": len(available)}


def read_arm(reader, entry, position, repo, original_valid=False):
    issues = Counter()
    mode = ORDER[position]
    check(issues, entry.get("position") == position and entry.get("observer_mode") == mode, "arm_order")
    prefix = "trial-" + str(position) + "-credit-" + mode + "/"
    payload = reader.read(prefix + "trial.json", 24 * MIB)
    marker = reader.read(prefix + "owners-closed.json", 4096)
    check(issues, marker.get("all_original_trial_close_calls_returned") is True, "owner_marker")
    watch = entry.get("watchdog", {})
    check(issues, watch.get("status") == "PASS" and watch.get("exit_code") == 0
          and watch.get("owner_retirement_verified") is True and watch.get("timeout") is False
          and watch.get("log_overflow") is False and watch.get("log_errors") == []
          and watch.get("watchdog_seconds") == 90, "watchdog_or_owner_retirement")
    check(issues, payload.get("qt_platform") == "windows" and payload.get("arm") == "none_qt"
          and payload.get("legacy_snapshot_policy") == "NONE" and payload.get("capture_enabled") is True,
          "native_windows_profile")
    check(issues, payload.get("outcome_overflow") == 0 and payload.get("cleanup_errors") == []
          and payload.get("record_retention", {}).get("capacity_reached") is False, "trial_overflow_or_cleanup")
    split = reader.read(prefix + "asset-split.json", 24 * MIB, 6000)
    check(issues, split.get("role") == "asset_split" and split.get("features", {}).get("passive_rpc_markers", False) is False,
          "asset_split_or_markers")
    split_sources = source_pair(split.get("source_before"), split.get("source_after"), repo, issues)
    for field in ("source_complete", "source_unchanged"):
        check(issues, split.get(field) is True, "asset_split_" + field)
    check(issues, split.get("instrumentation_disabled") is False and split.get("diagnostic_errors") == 0
          and split.get("dropped_rows") == 0 and split.get("outstanding_associations") == {"requests": 0, "replies": 0, "tokens": 0}, "asset_split_incomplete")
    fingerprints = {(row.get("asset_bytes"), row.get("asset_sha256"))
                    for row in split["rows"] if row.get("stage") == "client.asset_rpc_split"}
    require(len(fingerprints) <= 1, "multiple_raw_asset_fingerprints")
    encoded_bytes, asset_sha = next(iter(fingerprints), (None, None))
    require(not fingerprints or (integer(encoded_bytes) and 0 < encoded_bytes <= 8 * MIB
            and isinstance(asset_sha, str) and HASH.fullmatch(asset_sha)), "asset_fingerprint_schema")
    raw_asset = {"asset_bytes": encoded_bytes, "asset_sha256": asset_sha,
                 "observed_rpc_rows": sum(row.get("stage") == "client.asset_rpc_split" for row in split["rows"])}
    fingerprint_complete = raw_asset["observed_rpc_rows"] == COUNT * 2 and len(fingerprints) == 1
    expected_fingerprint = {"complete": fingerprint_complete, "calls": raw_asset["observed_rpc_rows"],
        "distinct_fingerprints": len(fingerprints), "asset_bytes": encoded_bytes if fingerprint_complete else None,
        "asset_sha256": asset_sha if fingerprint_complete else None}
    check(issues, entry.get("asset_fingerprint") == expected_fingerprint, "asset_fingerprint_summary_mismatch")
    read_attempts = sum(consumer.get("stats", {}).get("decoded", 0) + consumer.get("stats", {}).get("read_failed", 0)
                        for consumer in payload.get("consumers", []))
    split_counters = split.get("counters", {})
    check(issues, type(read_attempts) is int and read_attempts == raw_asset["observed_rpc_rows"]
          and all(split_counters.get(key) == read_attempts for key in ("client_calls_started", "client_calls_finished")),
          "raw_rpc_decode_counter_mismatch")
    check(issues, isinstance(payload.get("input_sha256"), str) and HASH.fullmatch(payload["input_sha256"])
          and entry.get("input_sha256") == payload["input_sha256"], "raw_input_fingerprint_mismatch")
    check(issues, entry.get("passive_markers_disabled") is True
          and entry.get("observer_configuration_matches") is True and entry.get("trace_complete") is True,
          "raw_control_observation_flags")
    arm_files = []
    for path in (reader.root / prefix).iterdir():
        arm_files.append(path)
        require(len(arm_files) <= 20, "arm_file_count_budget")
    traces = sorted(path for path in arm_files if re.fullmatch(r"trace-(?:execution|job|owner|exporter)-[0-9]+\.json", path.name))
    require(not any(path.name.startswith("trace-") and path not in traces for path in arm_files), "unexpected_base_trace_file")
    require(len(traces) == 5, "base_trace_file_denominator")
    roles = Counter()
    trace_summaries, raw_traces = [], []
    for path in traces:
        raw = reader.read(prefix + path.name, 24 * MIB, 24000)
        roles[raw.get("role")] += 1
        raw_traces.append(raw)
        check(issues, raw.get("dropped_rows") == 0, "base_trace_dropped")
        check(issues, all(clocks(row) for row in raw["rows"]), "base_trace_clock")
        trace_summaries.append({"file": path.name, **pick(raw, ("role", "pid", "row_limit", "dropped_rows")), "rows": len(raw["rows"])})
    check(issues, roles == {"execution": 1, "job": 1, "owner": 1, "exporter": 2}, "base_trace_roles")
    source_rows = outcomes(payload, issues)
    consumers, windows = consumer_tables(payload, issues, original_valid)
    base_coverage = base_trace_coverage(payload, raw_traces, entry, raw_asset["observed_rpc_rows"],
                                        encoded_bytes, issues, original_valid)
    result = {"position": position, "observer_mode": mode,
              "identity": pick(payload, ("job", "mode", "arm", "qt_platform", "legacy_snapshot_policy", "accepted_policy", "input_sha256")),
              "runtime_instance_id": payload.get("request", {}).get("expected_runtime_instance_id"),
              "watchdog": pick(watch, ("status", "exit_code", "timeout", "watchdog_seconds", "log_overflow", "log_limit_bytes", "log_bytes", "duration_seconds", "log_sha256", "owner_retirement_verified")),
              "original_control_fields": pick(entry, ("exact_ordinal_coverage", "trace_complete", "trace_roles", "trace_dropped_rows", "asset_fingerprint", "split_accounting", "source_outcomes", "delivery", "credit_identity_coverage", "capture_identity_coverage", "capture_export_join", "observer_configuration_matches", "passive_markers_disabled")),
              "trial_summary": pick(payload, ("job_status", "expected", "executed", "achieved_hz", "max_schedule_lateness_ms", "mean_execution_ms", "p95_execution_ms", "denominators", "pace_denominators", "phases", "timestamps", "record_retention", "outcome_overflow", "export")),
              "sources": source_rows, "consumers": consumers, "windows": windows,
              "base_traces": trace_summaries, "resources": resource_summary(payload, issues)}
    result["base_raw_coverage"] = base_coverage
    result["original_control_fields"]["split_accounting"] = pick(entry.get("split_accounting", {}),
        ("status", "issues", "expected_total_calls", "observed_call_ids", "expected_measured_calls", "joined_measured_calls", "rejected_calls", "channel_coverage"))
    result["raw_asset_fingerprint"] = raw_asset
    result["asset_split_sources"] = split_sources
    paths = sorted(path for path in arm_files if re.fullmatch(r"capture-credit-[0-9]+\.json", path.name))
    require(not any(path.name.startswith("capture-credit-") and path not in paths for path in arm_files), "unexpected_capture_file")
    if mode == "on":
        require(len(paths) == 1, "capture_file_denominator")
        capture = reader.read(prefix + paths[0].name, MIB, 512)
        export = reader.read(prefix + "export-credit.json", 12 * MIB, 6000)
        check(issues, capture.get("role") == "capture_credit" and export.get("role") == "export_credit", "probe_roles")
        result["capture_probe"] = observer_metadata(capture, repo, issues, capture=True)
        result["parent_probe"] = observer_metadata(export, repo, issues)
        for summary in (payload.get("export_credit_trace", {}), payload.get("capture_credit_trace", {}), capture.get("summary", {})):
            check(issues, summary.get("enabled") is True and summary.get("complete") is True, "probe_summary_incomplete")
        result["probe_coverage"] = probes(payload, capture, export, encoded_bytes, issues)
        result["capture_rows"] = table(capture["rows"], ACQUIRE_FIELDS, 512)
        result["parent_rows"] = parent_table(export["rows"])
    else:
        check(issues, not paths and not (reader.root / prefix / "export-credit.json").exists()
              and payload.get("export_credit_trace") is None and payload.get("capture_credit_trace") is None,
              "off_probe_present")
    result.update(observation_status="INVALID" if issues else "COMPLETE", issues=dict(issues))
    return result


def build_report(evidence, repo, expected_sha, original_step):
    reader = Reader(evidence)
    repo = Path(repo).resolve()
    manifest = reader.read("evidence.json", 16 * MIB)
    issues = Counter()
    guard = reader.read("hosted-guard.json", 16 * 1024)
    guard_sources = source_pair(guard.get("source_before"), guard.get("source_after"), repo, issues)
    check(issues, set(guard["source_before"]) == {"scripts/r3_hosted_credit_guard.py", "scripts/r3_export_credit_report.py",
          ".github/workflows/runtime-credit-diagnostics.yml"} and guard.get("source_stable") is True, "hosted_guard_source_scope")
    check(issues, guard.get("role") == "hosted_credit_guard" and guard.get("schema_version") == 1
          and guard.get("status") == "COMPLETE" and guard.get("setup_verified") is True
          and guard.get("assignment_verified") is True and guard.get("current_process_only") is True
          and guard.get("handle_closed") is True and guard.get("control_started") is True
          and guard.get("kill_on_job_close") is False and guard.get("errors") == [], "hosted_guard_incomplete")
    check(issues, all(guard.get(key) == 4 * 1024 ** 3 for key in
                     ("configured_job_memory_bytes", "readback_job_memory_bytes", "final_job_memory_bytes"))
          and all(guard.get(key) == 0x200 for key in ("configured_limit_flags", "readback_limit_flags", "final_limit_flags"))
          and guard.get("configured_priority_class") == guard.get("readback_priority_class") == 0x4000,
          "hosted_guard_readback_mismatch")
    check(issues, integer(guard.get("peak_job_memory_bytes")) and integer(guard.get("peak_process_memory_bytes"))
          and guard.get("memory_limit_violation_status") == "NOT_ASSESSED", "hosted_guard_peak_or_interpretation")
    check(issues, type(guard.get("original_control_exit_code")) is int
          and type(guard.get("wrapper_exit_code")) is int
          and guard["original_control_exit_code"] == guard["wrapper_exit_code"]
          and ((guard["original_control_exit_code"] == 0) == (original_step == "success")), "hosted_guard_exit_disagreement")
    require(isinstance(expected_sha, str) and re.fullmatch(r"[0-9a-f]{40,64}", expected_sha), "expected_sha_missing")
    check(issues, manifest.get("head") == expected_sha, "checkout_sha_mismatch")
    check(issues, manifest.get("dirty") == "", "dirty_source")
    check(issues, manifest.get("experiment") == "export_credit_lifetime_abba_v2", "control_version")
    check(issues, manifest.get("configuration") == {"count": COUNT, "warmup": 8,
          "observer_order": list(ORDER), "qt_platform": "windows"}, "control_configuration")
    check(issues, manifest.get("source_stable") is True, "control_source_unstable")
    before, after = manifest.get("source_before", {}), manifest.get("source_after", {})
    source = source_pair(before.get("files"), after.get("files"), repo, issues)
    for identity in (before, after):
        check(issues, identity.get("digest") == digest(json.dumps(identity["files"], sort_keys=True).encode()), "source_digest_mismatch")
    source.update(before_digest=before.get("digest"), after_digest=after.get("digest"))
    require(isinstance(manifest.get("trials"), list) and len(manifest["trials"]) == 4, "control_trial_denominator")
    arms = [read_arm(reader, entry, index, repo, manifest.get("measurement_status") == "VALID")
            for index, entry in enumerate(manifest["trials"])]
    for arm in arms:
        check(issues, arm["observation_status"] == "COMPLETE", "arm_" + str(arm["position"]) + "_invalid")
    raw_inputs = {arm["identity"].get("input_sha256") for arm in arms}
    raw_assets = {(arm["raw_asset_fingerprint"]["asset_bytes"], arm["raw_asset_fingerprint"]["asset_sha256"])
                  for arm in arms if arm["raw_asset_fingerprint"]["asset_bytes"] is not None}
    check(issues, len(raw_inputs) == 1 and len(raw_assets) <= 1, "raw_cross_arm_fingerprint_mismatch")
    entry_fingerprints = {(entry.get("input_sha256"), entry.get("asset_fingerprint", {}).get("asset_bytes"),
                          entry.get("asset_fingerprint", {}).get("asset_sha256")) for entry in manifest["trials"]}
    reconstructed_identical = len(entry_fingerprints) == 1 and all(
        entry.get("asset_fingerprint", {}).get("complete") is True for entry in manifest["trials"])
    check(issues, manifest.get("identical_input_and_asset") is reconstructed_identical,
          "identical_input_asset_claim_mismatch")
    check(issues, manifest.get("measurement_status") in ("VALID", "INVALID")
          and manifest.get("performance_status") == "NOT_ASSESSED", "unfinished_control")
    check(issues, original_step in ("success", "failure"), "original_control_not_run")
    check(issues, not (original_step == "success" and manifest.get("measurement_status") != "VALID"), "step_measurement_disagreement")
    if manifest.get("measurement_status") == "VALID":
        check(issues, original_step == "success" and manifest.get("identical_input_and_asset") is True
              and all(arm["original_control_fields"].get("exact_ordinal_coverage") is True
                      and arm.get("probe_coverage", {}).get("strict_full_image_coverage", True) for arm in arms),
              "valid_measurement_coverage_disagreement")
    report = {"schema_version": 1, "role": "hosted_export_credit_diagnostic", "stdout_limit_bytes": REPORT_BYTES,
              "input_limits": {"aggregate_bytes": INPUT_BYTES, "files": INPUT_FILES, "arm_entries": 20},
              "input_bytes_read": reader.total_bytes,
              "hosted_guard_sources": guard_sources,
              "hosted_guard": pick(guard, ("schema_version", "role", "status", "configured_job_memory_bytes", "configured_limit_flags",
                  "configured_priority_class", "priority_name", "current_process_only", "kill_on_job_close", "assignment_verified",
                  "setup_verified", "handle_closed", "control_started", "original_control_exit_code", "wrapper_exit_code",
                  "readback_limit_flags", "readback_job_memory_bytes", "readback_priority_class", "final_limit_flags",
                  "final_job_memory_bytes", "peak_job_memory_bytes", "peak_process_memory_bytes", "memory_limit_violation_status")),
              "observation_status": "INVALID" if issues else "COMPLETE", "issues": dict(issues),
              "original_step_outcome": original_step, "measurement_status": manifest.get("measurement_status"),
              "performance_status": manifest.get("performance_status"), "expected_sha": expected_sha,
              "control": pick(manifest, ("experiment", "head", "python", "os", "configuration", "bounds", "load", "started_ns", "finished_ns", "source_stable", "identical_input_and_asset")),
              "source_identity": source, "arms": arms, "raw_files": reader.provenance,
              "raw_cross_arm_fingerprints": {"input_sha256_values": sorted(raw_inputs),
                  "asset_fingerprints": [{"asset_bytes": size, "asset_sha256": sha} for size, sha in sorted(raw_assets)],
                  "original_identical_claim_reconstructed": reconstructed_identical},
              "interpretation": "COMPLETE means bounded observations only. Original step failure and INVALID measurement remain unchanged. Missing/rejected images retain the 96-input denominator. Null cells are absent raw fields, never fabricated export boundaries. Span values are inclusive and clocks retain process origin; no GIL ownership, pure lock wait, prior credit holder, disk latency, or performance PASS is inferred. Warmup records remain present. Raw row order is retained by raw_row_index. Free text is represented by UTF-8 byte length and SHA256; logs and workload paths are excluded."}
    return report


def transport(report):
    """Complete deterministic ASCII framing; chunks may split JSON escapes.

    Concatenate payloads in strict ordinal order before parsing JSON. Header
    and terminal metadata cover the exact unframed content, not a summary.
    The cap includes every framing byte and newline, with no truncation.
    """
    content = encoded(report)
    chunks = [content[index:index + CHUNK_BYTES].decode("ascii") for index in range(0, len(content), CHUNK_BYTES)]
    metadata = encoded({"schema_version": 1, "parts": len(chunks), "content_bytes": len(content),
                        "content_sha256": digest(content)}).decode("ascii")
    lines = ["CREDIT_REPORT_BEGIN " + metadata]
    lines += ["CREDIT_REPORT_PART " + str(index) + "/" + str(len(chunks)) + " " + chunk
              for index, chunk in enumerate(chunks, 1)]
    lines.append("CREDIT_REPORT_END " + metadata)
    output = "\n".join(lines) + "\n"
    require(len(output.encode("ascii")) <= REPORT_BYTES, "complete_report_exceeds_stdout_budget")
    return output


def decode_report(output):
    """Strict read-only reassembly helper for copied job-log framing."""
    require(isinstance(output, str) and len(output.encode("ascii")) <= REPORT_BYTES, "report_transport_budget")
    lines = output.splitlines()
    require(len(lines) >= 3 and lines[0].startswith("CREDIT_REPORT_BEGIN ")
            and lines[-1].startswith("CREDIT_REPORT_END "), "report_integrity_marker_missing")
    first = json.loads(lines[0].removeprefix("CREDIT_REPORT_BEGIN "), object_pairs_hook=pairs)
    last = json.loads(lines[-1].removeprefix("CREDIT_REPORT_END "), object_pairs_hook=pairs)
    require(first == last and first.get("schema_version") == 1 and first.get("parts") == len(lines) - 2,
            "report_part_denominator")
    chunks = []
    for index, line in enumerate(lines[1:-1], 1):
        prefix = "CREDIT_REPORT_PART " + str(index) + "/" + str(first["parts"]) + " "
        require(line.startswith(prefix), "report_part_missing_or_reordered")
        chunk = line[len(prefix):]
        require(0 < len(chunk.encode("ascii")) <= CHUNK_BYTES, "report_part_size")
        chunks.append(chunk)
    content = "".join(chunks).encode("ascii")
    require(len(content) == first.get("content_bytes") and digest(content) == first.get("content_sha256"),
            "report_content_integrity")
    return json.loads(content, object_pairs_hook=pairs)


def emit(report):
    sys.stdout.write(transport(report))
    sys.stdout.flush()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--expected-sha", default=os.environ.get("GITHUB_SHA", ""))
    parser.add_argument("--original-step", choices=("success", "failure", "cancelled", "skipped", "unknown"), default="unknown")
    args = parser.parse_args(argv)
    report = None
    try:
        report = build_report(args.evidence, args.repo, args.expected_sha, args.original_step)
        emit(report)
        return int(report["observation_status"] != "COMPLETE")
    except Exception as error:
        emit({"schema_version": 1, "role": "hosted_export_credit_diagnostic", "observation_status": "INVALID",
              "measurement_status": report.get("measurement_status") if report else "INVALID",
              "performance_status": "NOT_ASSESSED", "original_step_outcome": args.original_step,
              "error_code": str(error) if isinstance(error, InvalidEvidence) else "malformed_or_unavailable_evidence",
              "report_rejected": True, "truncated": False})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
