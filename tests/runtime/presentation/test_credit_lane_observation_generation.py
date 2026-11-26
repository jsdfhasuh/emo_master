"""The bounded diagnostic never assigns a late release to a new lane generation."""
import json

from scripts.r3_capture_credit_trace import CaptureCreditTrace
from scripts.r3_export_credit_trace import ExportCreditTrace


class _Pool:
    pass


def _identity(parent, pool):
    return dict(job_id="job", result_key="result", source_id="image",
                pool_id=parent.pool_id(pool), slot=0, lane=0)


def testCreditObserverPreservesLegacyRowsWithSameCrossProcessTag():
    pool = _Pool()
    parent = ExportCreditTrace()
    capture = CaptureCreditTrace()
    descriptors = [{"lane": 0}, {"lane": 1}]
    parent.configure_tags(pool, 0, descriptors)
    details = _identity(parent, pool)
    parent.record("parent.credit_release", (13, None), (14, None), parent.freeze_lane(details))
    details.pop("pool_id")
    capture.record(dict(details, result_ordinal=22, _trace_lane=descriptors[0]["_credit_trace_lane"]),
                   (10, None), (11, None), "OK", False)
    assert parent.lane_boundaries[0]["lane_tag"] == capture.lane_boundaries[0]["lane_tag"]
    assert not capture.lane_boundaries[0]["acquired"]
    assert "_trace_lane" not in capture.rows[0] and "lane_tag" not in parent.rows[0]
    assert json.loads(json.dumps(descriptors))[0]["_credit_trace_lane"][1] == 1


def testCreditObserverDoesNotAssignReconfiguredReleaseToNewGeneration():
    pool = _Pool()
    parent = ExportCreditTrace()
    descriptor = [{"lane": 0}]
    parent.configure_tags(pool, 0, descriptor)
    pending = parent.freeze_lane(_identity(parent, pool))
    replacement = [{"lane": 0}]
    parent.configure_tags(pool, 0, replacement)
    assert descriptor[0]["_credit_trace_lane"][0] == replacement[0]["_credit_trace_lane"][0]
    assert replacement[0]["_credit_trace_lane"][1] == 2
    parent.record("parent.credit_release", (20, None), (21, None), pending)
    assert parent.lane_boundaries[-1]["lane_tag"] is None
    assert parent.lane_boundaries[-1]["tag_status"] == "ambiguous_reconfigured"
