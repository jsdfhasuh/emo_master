"""Composite ROI masks retain the established single-region crop contract."""
from copy import deepcopy
import json
from pathlib import Path

import numpy as np
import pytest

from emo_master.core.contracts.geometry2d import BBox2D
from emo_master.plugins.builtins.roi.operator import RoiOperator


def box(x=1, y=1, width=6, height=6, purpose="keep", **extra):
    return dict(roiType="bbox", x=x, y=y, width=width, height=height, purpose=purpose, **extra)


def run(regions, **extra):
    image = np.full((10, 12, 3), 180, np.uint8)
    return RoiOperator().executeNode({"image": image}, dict(roiType="regions", regions=regions, padValue=13, **extra), {})


def testUnionThenExclusionsAreIndependentOfRegionOrder():
    regions = [box(), box(x=5, y=3), box(x=3, y=3, width=4, height=2, purpose="exclude")]
    expected = np.zeros((10, 12), np.uint8)
    expected[1:7, 1:7] = 255
    expected[3:9, 5:11] = 255
    expected[3:5, 3:7] = 0
    original = deepcopy(regions)
    for entries in (regions, list(reversed(regions))):
        result = run(entries)
        assert result["status"] == "ok"
        outputs = result["outputs"]
        np.testing.assert_array_equal(outputs["fullMask"], expected)
        np.testing.assert_array_equal(outputs["croppedMask"], expected[1:9, 1:11])
        assert np.all(outputs["maskedImage"][expected == 0] == 13)
        assert np.all(outputs["maskedImage"][expected != 0] == 180)
        assert outputs["croppedImage"].shape == (8, 10, 3)
        assert BBox2D.fromPayload(outputs["croppedFrame"]).coordinateSpace.mapPointToSource(0, 0) == (1, 1)
        envelope = BBox2D.fromPayload(outputs["roi"])
        assert (envelope.x, envelope.y, envelope.width, envelope.height) == (1, 1, 10, 8)
    assert regions == original


@pytest.mark.parametrize("regions", [[], [box(enabled=False)], [box(purpose="exclude", enabled=False)]])
def testNoEnabledKeepRegionsStartsWithFullImage(regions):
    result = run(regions)
    assert result["status"] == "ok"
    assert np.all(result["outputs"]["fullMask"] == 255)
    assert result["outputs"]["croppedImage"].shape == (10, 12, 3)


def testExcludeOnlyAndDisabledRegions():
    result = run([box(purpose="exclude"), box(x=8, y=8, width=1, height=1, purpose="exclude", enabled=False)])
    mask = result["outputs"]["fullMask"]
    assert np.all(mask[1:7, 1:7] == 0)
    assert mask[8, 8] == 255
    assert np.count_nonzero(mask) == 120-36


@pytest.mark.parametrize("region", [
    box(x=-2, width=8),
    dict(roiType="rotatedBox", centerX=5, centerY=5, width=6, height=4, angleDegrees=30),
    dict(roiType="polygon", points=[dict(x=1, y=1), dict(x=8, y=1), dict(x=5, y=7)]),
])
def testSingleKeepIsPixelEquivalentToLegacyRoi(region):
    image = np.arange(120, dtype=np.uint8).reshape(10, 12)
    op = RoiOperator()
    old = op.executeNode({"image": image}, dict(region, padValue=13), {})
    new = op.executeNode({"image": image}, dict(roiType="regions", regions=[region], padValue=13), {})
    assert old["status"] == new["status"] == "ok"
    for port in ("croppedImage", "croppedMask", "maskedImage", "fullMask"):
        np.testing.assert_array_equal(old["outputs"][port], new["outputs"][port])
    for port in ("croppedFrame", "maskedFrame", "roi"):
        assert old["outputs"][port] == new["outputs"][port]


@pytest.mark.parametrize("regions", [None, {}, [1], [box(purpose="wrong")], [box(enabled=1)],
    [box(width=0)], [dict(roiType="regions", regions=[])], [box(name=123)],
    [dict(roiType="polygon", points=[dict(x=1, y=1)] * 3)],
])
def testInvalidCompositeConfigurationIsRejected(regions):
    assert run(regions)["error"]["code"] == "E_PARAM_INVALID"


def testFullyExcludedRegionFailsWithoutReturningStalePixels():
    result = run([box(), box(purpose="exclude")])
    assert result["status"] == "error"
    assert result["error"]["code"] == "E_INPUT_SHAPE"
    assert "outputs" not in result


def testManifestMatchesOperatorSchema():
    manifest = json.loads(Path("src/emo_master/plugins/builtins/roi/manifest.json").read_text(encoding="utf-8"))
    assert manifest["paramSchema"] == RoiOperator.meta.paramSchema
    assert manifest["version"] == RoiOperator.meta.version
