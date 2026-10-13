"""Pure-data audit tests; never run a workload or reinterpret original failures."""
from copy import deepcopy
import hashlib
import json

import pytest

from scripts import windows_image_integrity_audit as audit


SHA = "a" * 40
IDENTITY = ("runtime_instance_id", "job_id", "scope_id", "invocation_id",
            "decision_runtime_instance_id", "decision_job_id")


def fixture():
    report = {"schema_version": 1, "role": "hosted_export_credit_diagnostic", "expected_sha": SHA,
        "control": {"head": SHA}, "observation_status": "COMPLETE", "issues": {},
        "source_identity": {"equal": True}, "original_step_outcome": "failure",
        "measurement_status": "INVALID", "performance_status": "NOT_ASSESSED", "arms": []}
    for position, mode in enumerate(audit.wire.ORDER):
        sources, raw, ui, decisions, identities = [], [], [], [], []
        for ordinal in range(1, 97):
            key, stamp = f"key-{ordinal}", ordinal * 100
            for source in ["image", "count"]:
                sources.append([key, ordinal, "COMPLETE", "runtime", source, "AVAILABLE", None, source == "image", 0])
            raw.append([key, ordinal, ["image"], {}, 0.1, stamp, stamp + 20, stamp + 20,
                        True, stamp - 10, 0.1, 0.1])
            ui.append([key, stamp + 20, stamp + 30, stamp - 10, stamp + 40])
            identities.append(["runtime", f"job-{position}", "root", f"call-{ordinal}", "runtime", f"job-{position}"])
            decisions.append([key, "APPLIED", 0, 0, ordinal, 0, key, ordinal - 1,
                              "COMPLETE", "COMPLETED", "b" * 64, "c" * 64])
        consumer = {"raw_records": {"columns": list(audit.wire.CONSUMER_FIELDS), "rows": raw},
                    "live_decisions": {"identity_columns": list(IDENTITY), "identities": identities,
                        "records": {"columns": list(audit.wire.DECISION_FIELDS), "rows": decisions}}}
        window = {"raw_records": {"columns": list(audit.wire.UI_FIELDS), "rows": ui}}
        report["arms"].append({"position": position, "observer_mode": mode, "identity": {"job": f"job-{position}"},
            "runtime_instance_id": "runtime", "sources": {"columns": list(audit.SOURCE_COLUMNS), "rows": sources},
            "consumers": [deepcopy(consumer), deepcopy(consumer)], "windows": [deepcopy(window), deepcopy(window)],
            "observation_status": "COMPLETE", "issues": {}})
    return report


def superseded(report):
    arm = report["arms"][0]
    for c in arm["consumers"]:
        raw = c["raw_records"]["rows"][1]
        raw[6:9] = [350, None, False]
        decision = c["live_decisions"]["records"]["rows"][1]
        decision[1], decision[4], decision[6] = "SUPERSEDED", 3, "key-3"
    for w in arm["windows"]:
        w["raw_records"]["rows"] = [x for x in w["raw_records"]["rows"] if x[0] != "key-2"]


def testCompleteLayerCoverageNeverTurnsOriginalFailureGreen():
    report = fixture()
    before = deepcopy(report)
    result = audit.audit(report, SHA)
    assert report == before
    assert result["original_result"] == {"original_step_outcome": "failure", "measurement_status": "INVALID", "performance_status": "NOT_ASSESSED"}
    assert len(result["arms"]) == 4
    for arm in result["arms"]:
        assert arm["source"]["covered"] == 96 and arm["source"]["formal_covered"] == 88
        assert [x["ordinal"] for x in arm["ordinals"]] == list(range(1, 97))
        assert arm["display_gaps"] == []


def testDirectSupersessionProofRetainsWarmupDenominatorAndMissingGUI():
    report = fixture()
    superseded(report)
    arm = audit.audit(report, SHA)["arms"][0]
    assert arm["source"]["covered"] == 96
    assert arm["consumers"][0]["live"]["missing"] == [2]
    assert arm["windows"][0]["painted"]["missing"] == [2]
    assert arm["ordinals"][1]["consumers"][0]["supersession"]["status"] == "PROVEN"
    assert arm["display_gaps"][0]["category"] == "LIVE_SUPERSESSION_PROVEN"


@pytest.mark.parametrize("field,value", [("scope_id", "other-scope"), ("task_generation", 1),
    ("decision_generation", 1), ("decision_job_id", "other-job"), ("decision_runtime_instance_id", "other-runtime")])
def testMismatchedHighIdentityDoesNotProveSupersession(field, value):
    report = fixture()
    superseded(report)
    c = report["arms"][0]["consumers"][0]["live_decisions"]
    if field in IDENTITY:
        c["identities"][2][list(IDENTITY).index(field)] = value
        # Keep cross-consumer source identities consistent, without making the
        # old/new identities within either consumer match.
        report["arms"][0]["consumers"][1]["live_decisions"]["identities"][2][list(IDENTITY).index(field)] = value
    else:
        c["records"]["rows"][2][list(audit.wire.DECISION_FIELDS).index(field)] = value
    arm = audit.audit(report, SHA)["arms"][0]
    assert arm["ordinals"][1]["consumers"][0]["supersession"]["status"] == "NOT_PROVEN"
    assert arm["display_gaps"][0]["root_cause_status"] == "NOT_PROVEN"


def testHighReceivedAfterOldDecisionDoesNotProveSupersession():
    report = fixture()
    superseded(report)
    c = report["arms"][0]["consumers"][0]["raw_records"]["rows"]
    c[1][6] = 250
    arm = audit.audit(report, SHA)["arms"][0]
    assert arm["ordinals"][1]["consumers"][0]["supersession"]["status"] == "NOT_PROVEN"


@pytest.mark.parametrize("gap", ["commit", "paint"])
def testGUIRecordAbsenceIsNotAutomaticallyCalledCoalescingOrSourceLoss(gap):
    report = fixture()
    for w in report["arms"][0]["windows"]:
        raw = w["raw_records"]["rows"]
        if gap == "commit":
            raw[:] = [x for x in raw if x[0] != "key-38"]
        else:
            raw[37][-1] = None
    arm = audit.audit(report, SHA)["arms"][0]
    assert arm["source"]["covered"] == arm["consumers"][0]["decoded"]["covered"] == 96
    assert arm["display_gaps"][0]["category"] == f"GUI_{gap.upper()}_NOT_RECORDED"
    assert arm["display_gaps"][0]["root_cause_status"] == "NOT_PROVEN"


def testUnavailableImageIsNotExcusedByDisplayContract():
    report = fixture()
    report["arms"][0]["sources"]["rows"][0][2] = "INCOMPLETE"
    report["arms"][0]["sources"]["rows"][0][5:8] = ["UNAVAILABLE", "BUDGET_EXCEEDED", False]
    for w in report["arms"][0]["windows"]:
        w["raw_records"]["rows"].pop(0)
    arm = audit.audit(report, SHA)["arms"][0]
    assert arm["source"]["covered"] == 95 and arm["source"]["missing"] == [1]
    assert arm["display_gaps"][0]["category"] == "SOURCE_INCOMPLETE"
    assert arm["ordinals"][0]["unavailable_reasons"] == ["BUDGET_EXCEEDED"]


def testSecondConsumerMissingCannotBeHiddenByCompleteFirstConsumer():
    report = fixture()
    c = report["arms"][0]["consumers"][1]
    c["raw_records"]["rows"].pop(0)
    c["live_decisions"]["records"]["rows"].pop(0)
    arm = audit.audit(report, SHA)["arms"][0]
    assert arm["consumers"][0]["received"]["covered"] == 96
    assert arm["consumers"][1]["received"]["missing"] == [1]


def testRepeatedCommitDoesNotInflateCoverage():
    report = fixture()
    report["arms"][0]["windows"][0]["raw_records"]["rows"] *= 2
    assert audit.audit(report, SHA)["arms"][0]["windows"][0]["committed"]["covered"] == 96


@pytest.mark.parametrize("damage", ["sha", "identity", "source_duplicate", "consumer_duplicate", "unknown_window",
    "source_overflow", "consumer_overflow", "window_overflow", "arm_order", "observation_issues"])
def testInconsistentOrOversizedEvidenceRejectsInsteadOfInventingClassification(damage):
    report = fixture()
    arm = report["arms"][0]
    if damage == "sha":
        report["control"]["head"] = "d" * 40
    elif damage == "identity":
        arm["consumers"][0]["live_decisions"]["identities"][0][0] = "other-runtime"
    elif damage == "source_duplicate":
        arm["sources"]["rows"][-1] = arm["sources"]["rows"][0]
    elif damage == "consumer_duplicate":
        arm["consumers"][0]["raw_records"]["rows"][-1] = arm["consumers"][0]["raw_records"]["rows"][0]
    elif damage == "unknown_window":
        arm["windows"][0]["raw_records"]["rows"][0][0] = "unknown"
    elif damage == "source_overflow":
        arm["sources"]["rows"] *= 2
    elif damage == "consumer_overflow":
        arm["consumers"][0]["raw_records"]["rows"] *= 2
    elif damage == "window_overflow":
        arm["windows"][0]["raw_records"]["rows"] *= 3
    elif damage == "arm_order":
        arm["observer_mode"] = "on"
    else:
        report["issues"] = {"missing_raw": 1}
    with pytest.raises(audit.wire.InvalidEvidence):
        audit.audit(report, SHA)


def testCLIReadOnlyExclusiveOutputAndOriginalStatuses(tmp_path):
    source, output = tmp_path / "source.json", tmp_path / "audit.json"
    source.write_text(json.dumps(fixture()), encoding="utf8")
    before = source.read_bytes()
    args = ["--report", str(source), "--expected-sha", SHA, "--output", str(output)]
    assert audit.main(args) == 0
    result = json.loads(output.read_bytes())
    assert source.read_bytes() == before
    assert result["input_sha256"] == hashlib.sha256(before).hexdigest()
    assert result["original_result"]["original_step_outcome"] == "failure"
    saved = output.read_bytes()
    assert audit.main(args) == 2 and output.read_bytes() == saved
    args[-1] = str(source)
    assert audit.main(args) == 2 and source.read_bytes() == before


@pytest.mark.parametrize("data", [b"x" * (audit.wire.REPORT_BYTES + 1), b'{"key":1,"key":2}', b'{"x":NaN}', b"{"],
                         ids=["oversized", "duplicate-key", "nonfinite", "malformed"])
def testCLIRejectsOversizeDuplicateAndMalformedInput(tmp_path, data):
    source, output = tmp_path / "source.json", tmp_path / "audit.json"
    source.write_bytes(data)
    assert audit.main(["--report", str(source), "--expected-sha", SHA, "--output", str(output)]) == 2
    assert not output.exists() and source.read_bytes() == data
