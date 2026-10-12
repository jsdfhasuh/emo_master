"""Replay preserved same-host producer/parent clocks; never execute a workload."""
import argparse
import hashlib
import json
from pathlib import Path


STAGES = ("parent.pipe_recv", "parent.asset_adopt", "parent.export_callback", "parent.credit_release")


def load(path):
    data = path.read_bytes()
    return json.loads(data), {"file": path.name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def correlate(capture, exports):
    attempts = sorted(capture, key=lambda row: row["start_ns"])
    result = []
    for attempt in attempts:
        if attempt.get("acquired") is not False:
            continue
        earlier = [row for row in attempts if row.get("acquired") is True
                   and row["end_ns"] <= attempt["start_ns"]
                   and all(row[key] == attempt[key] for key in ("runtime_id", "job_id", "slot", "lane"))]
        previous = max(earlier, key=lambda row: row["start_ns"]) if earlier else None
        item = {"rejection": attempt, "previous_accepted": previous, "classification": "UNRESOLVED"}
        if previous is not None:
            rows = [row for row in exports if row.get("job_id") == previous["job_id"]
                    and row.get("result_key") == previous["result_key"]
                    and row.get("source_id") == previous["source_id"]
                    and row.get("slot") == previous["slot"] and row.get("lane") == previous["lane"]
                    and row.get("stage") in STAGES and row.get("outcome") == "OK"]
            phases = {}
            for stage in STAGES:
                matched = [row for row in rows if row["stage"] == stage]
                if len(matched) == 1:
                    phases[stage] = {key: matched[0][key] for key in ("start_ns", "end_ns")}
            item["previous_parent_phases"] = phases
            item["relative_to_rejection_ms"] = {
                stage: {key: (value - attempt["start_ns"]) / 1e6 for key, value in bounds.items()}
                for stage, bounds in phases.items()}
            release, reply, adopt = (phases.get(stage) for stage in
                                    ("parent.credit_release", "parent.pipe_recv", "parent.asset_adopt"))
            # A prior acceptance alone does not prove an outstanding holder.
            if release and release["start_ns"] >= attempt["end_ns"]:
                if reply and reply["start_ns"] >= attempt["end_ns"]:
                    item["classification"] = "BEFORE_PARENT_COMPLETION_REPLY"
                elif (reply and reply["end_ns"] <= attempt["start_ns"] and adopt
                      and adopt["start_ns"] <= attempt["start_ns"]
                      and attempt["end_ns"] <= adopt["end_ns"]):
                    item["classification"] = "RAW_CREDIT_HELD_DURING_ASSET_ADOPTION"
        result.append(item)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local-trial", type=Path, required=True)
    parser.add_argument("--public-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    capture, captureHash = load(next(args.local_trial.glob("capture-credit-*.json")))
    parent, parentHash = load(args.local_trial / "export-credit.json")
    public, publicHash = load(args.public_report)
    records = [{"origin": "historical-local-trial-2", "inputs": [captureHash, parentHash],
                "refusals": correlate(capture["rows"], parent["rows"])}]
    for arm in public["arms"]:
        if arm["observer_mode"] != "on":
            continue
        table = arm["capture_rows"]
        captureRows = [dict(zip(table["columns"], row)) for row in table["rows"]]
        table, parentRows = arm["parent_rows"], []
        for group in table["groups"]:
            identity = dict(zip(table["identity_columns"], group["identity"]))
            for values in group["rows"]:
                row = dict(identity, **dict(zip(table["row_columns"], values)))
                row["stage"] = table["stage_dictionary"][row["stage"]]
                parentRows.append(row)
        records.append({"origin": "hosted-38223062630-arm-" + str(arm["position"]),
                        "inputs": [publicHash], "refusals": correlate(captureRows, parentRows)})
    report = {"clock": "same-host Python 3.10 perf_counter_ns for worker and parent",
              "scope": "Observed arms only. Before reply does not identify encoding versus scheduling delay. "
                       "No claim about the unobserved arm or a guarantee of future admission.", "records": records}
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2)


if __name__ == "__main__":
    main()
