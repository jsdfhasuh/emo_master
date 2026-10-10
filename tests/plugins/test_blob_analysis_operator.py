from __future__ import annotations

import numpy as np

from emo_master.core.contracts.geometry2d import BlobCollection
from emo_master.plugins.builtins.blob_analysis.operator import BlobAnalysisOperator


def testBlobAnalysisReturnsTypedCollectionAndFilteredMask() -> None:
    image = np.zeros((10, 12, 3), dtype=np.uint8)
    image[1:3, 1:4] = 255
    image[5:8, 7:10] = 255

    result = BlobAnalysisOperator().executeNode(
        {"image": image},
        {"threshold": 127, "minArea": 1},
        {},
    )

    assert result["status"] == "ok"
    outputs = result["outputs"]
    assert isinstance(outputs, dict)
    blobs = BlobCollection.fromPayload(outputs["blobs"])
    assert [item.area for item in blobs.items] == [6.0, 9.0]
    assert (
        blobs.items[0].bbox.x,
        blobs.items[0].bbox.y,
        blobs.items[0].bbox.width,
        blobs.items[0].bbox.height,
    ) == (1.0, 1.0, 3.0, 2.0)
    assert blobs.coordinateSpace.imageWidth == 12
    assert blobs.coordinateSpace.imageHeight == 10
    mask = outputs["mask"]
    assert isinstance(mask, np.ndarray)
    assert int(np.count_nonzero(mask)) == 15
    assert "overlay" not in outputs


def testBlobAnalysisFiltersByAreaAndCanDrawOverlay() -> None:
    image = np.zeros((8, 8), dtype=np.uint8)
    image[0:2, 0:2] = 255
    image[4:7, 4:7] = 255

    result = BlobAnalysisOperator().executeNode(
        {"image": image},
        {"minArea": 5, "drawOverlay": True, "includeContour": True},
        {},
    )

    outputs = result["outputs"]
    assert isinstance(outputs, dict)
    blobs = BlobCollection.fromPayload(outputs["blobs"])
    assert len(blobs.items) == 1
    assert blobs.items[0].area == 9.0
    assert blobs.items[0].contour is not None
    assert int(np.count_nonzero(outputs["mask"])) == 9
    overlay = outputs["overlay"]
    assert isinstance(overlay, np.ndarray)
    assert overlay.shape == (8, 8, 3)


def testBlobAnalysisConnectivityControlsDiagonalComponents() -> None:
    image = np.zeros((3, 3), dtype=np.uint8)
    image[0, 0] = 255
    image[1, 1] = 255
    operator = BlobAnalysisOperator()

    four = operator.executeNode({"image": image}, {"connectivity": 4}, {})
    eight = operator.executeNode({"image": image}, {"connectivity": 8}, {})

    assert len(BlobCollection.fromPayload(four["outputs"]["blobs"]).items) == 2
    assert len(BlobCollection.fromPayload(eight["outputs"]["blobs"]).items) == 1


def testBlobAnalysisUsesExternalBinaryMaskInsteadOfThresholdingImage() -> None:
    image = np.zeros((8, 8, 3), dtype=np.uint8)
    image[1:4, 1:4] = 50
    image[4:7, 4:7] = 250
    mask = np.zeros((8, 8), dtype=np.uint8)
    mask[1:4, 1:4] = 1

    result = BlobAnalysisOperator().executeNode(
        {"image": image, "mask": mask},
        {"threshold": 200, "minArea": 1},
        {},
    )

    assert result["status"] == "ok"
    blobs = BlobCollection.fromPayload(result["outputs"]["blobs"])
    assert len(blobs.items) == 1
    assert blobs.items[0].area == 9.0
    assert blobs.items[0].bbox.x == 1.0
    assert int(np.count_nonzero(result["outputs"]["mask"])) == 9
    assert result["metrics"]["threshold"] is None
    assert result["metrics"]["maskSource"] == "external"


def testBlobAnalysisRejectsInvalidExternalMask() -> None:
    operator = BlobAnalysisOperator()
    image = np.zeros((4, 4, 3), dtype=np.uint8)

    cases = [
        operator.executeNode({"image": image, "mask": None}, {}, {}),
        operator.executeNode(
            {"image": image, "mask": np.zeros((4, 4), dtype=np.float32)},
            {},
            {},
        ),
        operator.executeNode(
            {"image": image, "mask": np.zeros((4, 4, 1), dtype=np.uint8)},
            {},
            {},
        ),
        operator.executeNode(
            {"image": image, "mask": np.zeros((3, 4), dtype=np.uint8)},
            {},
            {},
        ),
    ]

    assert [item["error"]["code"] for item in cases] == [
        "E_INPUT_TYPE",
        "E_INPUT_TYPE",
        "E_INPUT_SHAPE",
        "E_INPUT_SHAPE",
    ]


def testBlobAnalysisRejectsInvalidInputAndParameters() -> None:
    operator = BlobAnalysisOperator()
    missing = operator.executeNode({}, {}, {})
    invalidImage = operator.executeNode(
        {"image": np.zeros((2, 2), dtype=np.float32)}, {}, {}
    )
    invalidParams = operator.executeNode(
        {"image": np.zeros((2, 2), dtype=np.uint8)},
        {"minArea": 10, "maxArea": 5},
        {},
    )

    assert missing["error"]["code"] == "E_INPUT_MISSING"
    assert invalidImage["error"]["code"] == "E_INPUT_TYPE"
    assert invalidParams["error"]["code"] == "E_PARAM_INVALID"
