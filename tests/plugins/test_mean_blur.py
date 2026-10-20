from dataclasses import asdict
import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from emo_master.plugins.builtins.blur.operator import BlurOperator


@pytest.mark.parametrize("shape", [(31, 43), (31, 43, 3)])
@pytest.mark.parametrize("kernel", [3, 7, 9])
def testMeanExactlyMatchesLegacyCvBlurAndPreservesPixels(shape, kernel):
    image = np.random.default_rng(92).integers(0, 256, shape, dtype=np.uint8)
    before = image.copy()
    out = BlurOperator().executeNode(dict(image=image), dict(mode="mean", kernelSize=kernel), {})
    assert out["status"] == "ok"
    np.testing.assert_array_equal(out["outputs"]["image"], cv2.blur(image, (kernel, kernel)))
    np.testing.assert_array_equal(image, before)
    assert not np.shares_memory(out["outputs"]["image"], image)


def testMeanSchemaMatchesManifestAndDefaultIsUnchanged():
    path = Path(__file__).resolve().parents[2] / "src/emo_master/plugins/builtins/blur/manifest.json"
    manifest = json.loads(path.read_bytes())
    for field, value in asdict(BlurOperator.meta).items():
        assert manifest[field] == value
    assert manifest["paramSchema"]["properties"]["mode"]["default"] == "gaussian"
    assert BlurOperator().validateParams(dict(mode="mean", kernelSize=4)) is not None
