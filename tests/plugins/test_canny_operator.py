import numpy as np

from emo_master.core.contracts.geometry2d import BBox2D
from emo_master.plugins.builtins.canny_edge.operator import CannyEdgeOperator


def testCannyOperatorHasEdgeOutput() -> None:
    image = np.zeros((64, 64), dtype=np.uint8)
    image[20:40, 20:40] = 255
    operatorInstance = CannyEdgeOperator()

    result = operatorInstance.executeNode(
        {"image": image},
        {
            "thresholdLow": 50,
            "thresholdHigh": 150,
            "apertureSize": 3,
            "l2Gradient": False,
        },
        {},
    )

    assert result["status"] == "ok"
    assert "edges" in result["outputs"]
    frame = BBox2D.fromPayload(result["outputs"]["frame"])
    assert (frame.width, frame.height) == (64.0, 64.0)
