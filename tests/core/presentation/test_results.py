import pytest

from emo_master.core.presentation.results import (
    AssetLease, ClosedResult, ClosedSource, FrameProvenance, ImageRef, ResultIdentity, ResultNotification,
)


def identity():
    return ResultIdentity(runtimeInstanceId="runtime", jobId="job", resultScopeId="scope",
                          invocationId="call", resultKey="result", resultOrdinal=3,
                          executionRevision="a" * 64, capturePlanRevision="b" * 64, mode="debug")


def testClosureIncludesFailedExportAndSeparatesCursor():
    result = ClosedResult(identity=identity(), expectedSourceIds=("image", "count"), sources=(
        ClosedSource(sourceId="image", state="UNAVAILABLE", reason="export timeout"),
        ClosedSource(sourceId="count", state="AVAILABLE", valueJson="4")),
        executionTerminal="COMPLETED", status="INCOMPLETE")
    notification = ResultNotification(messageCursor=100, result=result)
    assert notification.result.identity.resultOrdinal == 3 and notification.messageCursor == 100
    assert ClosedResult.model_validate_json(result.model_dump_json()) == result
    with pytest.raises(ValueError, match="status contradicts"):
        ClosedResult.model_validate({**result.model_dump(), "status": "COMPLETE"})
    with pytest.raises(ValueError, match="every expected"):
        ClosedResult.model_validate({**result.model_dump(), "expectedSourceIds": ("image",)})


def testAvailableImageMustAlreadyBelongToResult():
    image = ImageRef(resourceId="asset", ownerResultKey="other", byteSize=5, sha256="a" * 64,
                     mimeType="image/png", provenance=FrameProvenance(
                         frameIdentity="f1", coordinateSpaceId="sensor", trust="unknown"))
    with pytest.raises(ValueError, match="owned"):
        ClosedResult(identity=identity(), expectedSourceIds=("image",), sources=(
            ClosedSource(sourceId="image", state="AVAILABLE", image=image),),
            status="COMPLETE", executionTerminal="COMPLETED")


@pytest.mark.parametrize("value", ["NaN", "Infinity", "not json"])
def testBadValuesCannotEnterClosedDTO(value):
    with pytest.raises(ValueError):
        ClosedSource(sourceId="count", state="AVAILABLE", valueJson=value)


def testNoStalePayloadOrUnboundedLease():
    with pytest.raises(ValueError):
        ClosedSource(sourceId="count", state="UNAVAILABLE", reason="failed", valueJson="4")
    with pytest.raises(ValueError):
        AssetLease(leaseId="l", resourceId="r", ttlMs=30001)
