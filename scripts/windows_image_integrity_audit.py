"""Read-only layer audit of an existing bounded R3 public report, not acceptance.

No application imports, workload, retries, deadline changes or original verdict
changes. A missing Qt record is not proof of a dropped source image or of normal
Qt coalescing. Only decision-time evidence can prove live supersession.
"""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import re
import sys

if __package__:
    from . import r3_export_credit_report as wire
else:
    import r3_export_credit_report as wire


SHA = re.compile(r"[0-9a-f]{40}\Z")
SOURCE_COLUMNS = ("result_key", "ordinal", "status", "mode", "source_id", "state",
                  "reason", "image_present", "detail_digest_index")
EXPECTED = set(range(1, 97))


def rows(table, columns, limit):
    wire.require(isinstance(table, dict) and table.get("columns") == list(columns), "audit_table_columns")
    data = table.get("rows")
    wire.require(isinstance(data, list) and len(data) <= limit
                 and all(isinstance(row, list) and len(row) == len(columns) for row in data), "audit_table_rows")
    return [dict(zip(columns, row)) for row in data]


def safe_id(value):
    wire.require(isinstance(value, str) and wire.SAFE_ID.fullmatch(value), "audit_identity")
    return value


def coverage(values):
    observed = set(values)
    return {"denominator": 96, "covered": len(observed & EXPECTED),
            "missing": sorted(EXPECTED - observed),
            "formal_denominator": 88, "formal_covered": len(observed & set(range(9, 97)))}


def decisions(consumer):
    packed = consumer.get("live_decisions")
    wire.require(packed is None or isinstance(packed, dict), "audit_decision_table")
    if packed is None or packed.get("status") == "NOT_RECORDED":
        return {}
    table = wire.expand_live_decisions(packed)
    identity_columns = ("runtime_instance_id", "job_id", "scope_id", "invocation_id",
                        "decision_runtime_instance_id", "decision_job_id")
    wire.require(table.get("identity_columns") == list(identity_columns), "audit_decision_identity_columns")
    identities = table.get("identities")
    wire.require(isinstance(identities, list) and len(identities) <= 128, "audit_decision_identities")
    for identity in identities:
        wire.require(isinstance(identity, list) and len(identity) == 6, "audit_decision_identity_width")
        for cell in identity:
            safe_id(cell)
    result = {}
    for row in rows(table["records"], wire.DECISION_FIELDS, 128):
        index = row.pop("identity_index")
        wire.require(type(index) is int and 0 <= index < len(identities), "audit_decision_identity_index")
        row.update(zip(identity_columns, identities[index]))
        key = safe_id(row["key"])
        wire.require(key not in result, "audit_duplicate_decision")
        wire.require(row["live_decision"] in {"APPLIED", "SUPERSEDED", "GENERATION_CHANGED",
                     "JOB_CHANGED", "RUNTIME_CHANGED", "SCOPE_EXPIRED"}, "audit_decision_kind")
        for field in ("task_generation", "decision_generation", "high_ordinal", "expired_ordinal"):
            wire.require(wire.integer(row[field]), "audit_decision_integer")
        if row["high_result_key"] is not None:
            safe_id(row["high_result_key"])
        for field in ("source_outcomes_sha256", "image_refs_sha256"):
            wire.require(isinstance(row[field], str) and wire.HASH.fullmatch(row[field]), "audit_decision_digest")
        result[key] = row
    return result


def supersession(row, decision, records, decision_rows):
    if not decision or decision["live_decision"] != "SUPERSEDED":
        return None
    high = records.get(decision["high_result_key"])
    high_decision = decision_rows.get(decision["high_result_key"])
    same_identity = high_decision and all(decision[k] == high_decision[k] for k in
        ("runtime_instance_id", "job_id", "scope_id", "task_generation", "decision_generation",
         "decision_runtime_instance_id", "decision_job_id"))
    proven = bool(high and same_identity and decision["task_generation"] == decision["decision_generation"]
        and decision["runtime_instance_id"] == decision["decision_runtime_instance_id"]
        and decision["job_id"] == decision["decision_job_id"]
        and decision["high_ordinal"] == high["ordinal"] > row["ordinal"] > decision["expired_ordinal"]
        and high["received_ns"] < row["decoded_ns"] and not row["applied_to_live"] and row["model_ns"] is None)
    return {"status": "PROVEN" if proven else "NOT_PROVEN", "high_result_key": decision["high_result_key"],
            "high_ordinal": decision["high_ordinal"],
            "high_received_before_old_decision_ns": row["decoded_ns"] - high["received_ns"] if high else None}


def audit_arm(arm, position):
    wire.require(arm.get("position") == position and arm.get("observer_mode") == wire.ORDER[position], "audit_arm_order")
    expected_job = safe_id(arm["identity"]["job"])
    expected_runtime = safe_id(arm["runtime_instance_id"])
    sources = rows(arm["sources"], SOURCE_COLUMNS, 192)
    source_by_ordinal = defaultdict(dict)
    key_ordinals = {}
    ordinal_keys = defaultdict(set)
    for row in sources:
        key = safe_id(row["result_key"])
        ordinal = row["ordinal"]
        wire.require(type(ordinal) is int and ordinal in EXPECTED, "audit_source_ordinal")
        wire.require(row["source_id"] in {"image", "count"} and row["state"] in {"AVAILABLE", "UNAVAILABLE"}, "audit_source_state")
        wire.require(row["status"] in {"COMPLETE", "INCOMPLETE"} and row["mode"] in {"runtime", "debug"}, "audit_source_status")
        wire.require(type(row["image_present"]) is bool, "audit_source_image_flag")
        if row["reason"] is not None:
            safe_id(row["reason"])
        wire.require(row["source_id"] not in source_by_ordinal[ordinal], "audit_duplicate_source")
        wire.require(key not in key_ordinals or key_ordinals[key] == ordinal, "audit_source_key_ordinal")
        key_ordinals[key] = ordinal
        ordinal_keys[ordinal].add(key)
        source_by_ordinal[ordinal][row["source_id"]] = row
    wire.require(all(len(keys) == 1 for keys in ordinal_keys.values()), "audit_multiple_result_keys")
    wire.require(isinstance(arm.get("consumers"), list) and len(arm["consumers"]) == 2
                 and isinstance(arm.get("windows"), list) and len(arm["windows"]) == 2, "audit_topology")
    consumers, records_by_consumer, decisions_by_consumer = [], [], []
    for consumer in arm["consumers"]:
        records, decision_rows = {}, decisions(consumer)
        for row in rows(consumer["raw_records"], wire.CONSUMER_FIELDS, 128):
            key = safe_id(row["key"])
            wire.require(key in key_ordinals and type(row["ordinal"]) is int
                         and row["ordinal"] == key_ordinals[key] and key not in records, "audit_consumer_join")
            wire.require(isinstance(row["decoded"], list) and set(row["decoded"]) <= {"image", "count"}
                         and isinstance(row["failures"], dict), "audit_consumer_image_state")
            wire.require(all(wire.integer(row[k]) for k in ("received_ns", "decoded_ns", "scope_ended_ns"))
                         and row["received_ns"] <= row["decoded_ns"], "audit_consumer_clock")
            wire.require(type(row["applied_to_live"]) is bool and (row["model_ns"] is None
                         or wire.integer(row["model_ns"]) and row["model_ns"] == row["decoded_ns"]), "audit_model_clock")
            wire.require(row["applied_to_live"] == (row["model_ns"] is not None), "audit_applied_clock")
            if key in decision_rows:
                decision = decision_rows[key]
                wire.require(decision["runtime_instance_id"] == expected_runtime and decision["job_id"] == expected_job, "audit_consumer_identity")
                wire.require((decision["live_decision"] == "APPLIED") == row["applied_to_live"], "audit_decision_applied")
            records[key] = row
        wire.require(set(decision_rows) <= set(records), "audit_decision_join")
        records_by_consumer.append(records)
        decisions_by_consumer.append(decision_rows)
        consumers.append({"received": coverage(r["ordinal"] for r in records.values()),
            "decoded": coverage(r["ordinal"] for r in records.values() if "image" in r["decoded"] and not r["failures"]),
            "live": coverage(r["ordinal"] for r in records.values() if r["applied_to_live"])})
    for key in set(decisions_by_consumer[0]) & set(decisions_by_consumer[1]):
        a, b = decisions_by_consumer[0][key], decisions_by_consumer[1][key]
        wire.require(all(a[k] == b[k] for k in ("runtime_instance_id", "job_id", "scope_id", "invocation_id",
                     "source_outcomes_sha256", "image_refs_sha256")), "audit_cross_consumer_identity")
    windows, window_rows = [], []
    for window in arm["windows"]:
        raw = rows(window["raw_records"], wire.UI_FIELDS, 256)
        by_key = defaultdict(list)
        for row in raw:
            wire.require(row["key"] in key_ordinals, "audit_window_join")
            wire.require(all(wire.integer(row[k]) for k in ("ready_ns", "gui_ns", "scope_end_ns"))
                         and row["ready_ns"] <= row["gui_ns"] and (row["paint_ns"] is None
                         or wire.integer(row["paint_ns"]) and row["paint_ns"] >= row["gui_ns"]), "audit_window_clock")
            owner = records_by_consumer[0].get(row["key"])
            wire.require(owner and owner["applied_to_live"] and row["ready_ns"] == owner["model_ns"], "audit_window_live_join")
            by_key[row["key"]].append(row)
        window_rows.append(by_key)
        windows.append({"committed": coverage(key_ordinals[r["key"]] for r in raw),
                        "painted": coverage(key_ordinals[r["key"]] for r in raw if r["paint_ns"] is not None)})
    details, gaps = [], []
    complete_sources = []
    for ordinal in range(1, 97):
        source_rows = source_by_ordinal[ordinal]
        complete = (set(source_rows) == {"image", "count"}
                    and all(r["state"] == "AVAILABLE" and r["status"] == "COMPLETE" for r in source_rows.values())
                    and source_rows["image"]["image_present"])
        if complete:
            complete_sources.append(ordinal)
        key = next(iter(ordinal_keys[ordinal]), None)
        detail = {"ordinal": ordinal, "result_key": key, "source_complete": bool(complete),
                  "unavailable_reasons": sorted({r["reason"] for r in source_rows.values() if r["reason"]}),
                  "consumers": [], "windows": []}
        for i, records in enumerate(records_by_consumer):
            row, decision = records.get(key), decisions_by_consumer[i].get(key)
            detail["consumers"].append({"received": row is not None,
                "decoded": bool(row and "image" in row["decoded"] and not row["failures"]),
                "live": bool(row and row["applied_to_live"]),
                "live_decision": decision["live_decision"] if decision else "NOT_RECORDED",
                "supersession": supersession(row, decision, records, decisions_by_consumer[i]) if row else None})
        for i, by_key in enumerate(window_rows):
            own = by_key.get(key, [])
            committed, painted = bool(own), any(r["paint_ns"] is not None for r in own)
            detail["windows"].append({"committed": committed, "painted": painted})
            if not painted:
                proof = detail["consumers"][0]["supersession"]
                category = ("SOURCE_INCOMPLETE" if not complete else
                    "CONSUMER_RECEIVE_OR_DECODE_INCOMPLETE" if not detail["consumers"][0]["decoded"] else
                    "LIVE_SUPERSESSION_PROVEN" if proof and proof["status"] == "PROVEN" else
                    "GUI_PAINT_NOT_RECORDED" if committed else
                    "GUI_COMMIT_NOT_RECORDED" if detail["consumers"][0]["live"] else "LIVE_NOT_APPLIED_UNEXPLAINED")
                later = sorted((r for k, rs in by_key.items() if key_ordinals[k] > ordinal for r in rs), key=lambda r: r["gui_ns"])
                gaps.append({"ordinal": ordinal, "window": i, "category": category,
                    "root_cause_status": "PROVEN_LIVE_DECISION" if category == "LIVE_SUPERSESSION_PROVEN" else "NOT_PROVEN",
                    "commit_ns": [r["gui_ns"] for r in own],
                    "next_commit_ordinal": key_ordinals[later[0]["key"]] if later else None,
                    "next_commit_ns": later[0]["gui_ns"] if later else None})
        details.append(detail)
    return {"position": position, "observer_mode": wire.ORDER[position],
            "runtime_instance_id": expected_runtime, "job_id": expected_job,
            "source": coverage(complete_sources), "consumers": consumers, "windows": windows,
            "display_gaps": gaps, "ordinals": details}


def audit(report, expected_sha):
    wire.require(isinstance(expected_sha, str) and SHA.fullmatch(expected_sha), "audit_expected_sha")
    wire.require(isinstance(report, dict) and report.get("schema_version") == 1
                 and report.get("role") == "hosted_export_credit_diagnostic", "audit_report_schema")
    wire.require(report.get("expected_sha") == expected_sha and isinstance(report.get("control"), dict)
                 and report["control"].get("head") == expected_sha, "audit_source_sha")
    wire.require(report.get("observation_status") == "COMPLETE" and report.get("issues") == {}
                 and isinstance(report.get("source_identity"), dict)
                 and report["source_identity"].get("equal") is True, "audit_incomplete_observations")
    wire.require(isinstance(report.get("arms"), list) and len(report["arms"]) == 4
                 and all(isinstance(a, dict) and a.get("issues") == {}
                         and a.get("observation_status") == "COMPLETE" for a in report["arms"]), "audit_arm_observations")
    wire.require(report.get("original_step_outcome") in {"success", "failure", "cancelled", "skipped"}
                 and report.get("measurement_status") in {"VALID", "INVALID"}
                 and report.get("performance_status") == "NOT_ASSESSED", "audit_original_status")
    return {"schema_version": 1, "role": "offline_image_layer_audit_not_acceptance", "expected_sha": expected_sha,
            "original_result": {k: report[k] for k in ("original_step_outcome", "measurement_status", "performance_status")},
            "arms": [audit_arm(arm, i) for i, arm in enumerate(report["arms"])],
            "boundaries": ["No workload executed; original verdict unchanged; all 96 ordinals include warmup.",
                "Windows share consumer0 hub in the fixed original R3 workload; not one network consumer per window.",
                "Missing GUI records alone prove neither capture loss nor Qt coalescing; exact GUI cause remains unproven.",
                "Source/image digests join the upstream sanitized report, not a fresh offline decode of original PNGs.",
                "This run cannot close historical budget refusals, timeouts, performance or long-term stability."]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        wire.require(not args.report.is_symlink() and args.report.is_file()
                     and args.report.resolve() != args.output.resolve(), "audit_input_path")
        with args.report.open("rb") as stream:
            data = stream.read(wire.REPORT_BYTES + 1)
        wire.require(0 < len(data) <= wire.REPORT_BYTES, "audit_input_budget")
        report = json.loads(data, object_pairs_hook=wire.pairs,
                            parse_constant=lambda _value: wire.require(False, "audit_nonfinite_json"))
        result = audit(report, args.expected_sha)
        result["input_sha256"] = hashlib.sha256(data).hexdigest()
        output = wire.encoded(result)
        wire.require(len(output) <= wire.REPORT_BYTES, "audit_output_budget")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("xb") as stream:
            stream.write(output)
        print(json.dumps({"status": "AUDIT_WRITTEN_NOT_ACCEPTANCE", "bytes": len(output), "original_result": result["original_result"]}))
        return 0
    except (wire.InvalidEvidence, OSError, ValueError, KeyError, TypeError, IndexError, AttributeError, RecursionError):
        print('{"status":"AUDIT_REJECTED_OR_OUTPUT_EXISTS"}', file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
