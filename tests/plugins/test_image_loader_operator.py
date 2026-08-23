from pathlib import Path

import cv2
import numpy as np

from emo_master.plugins.builtins.image_loader.operator import ImageLoaderOperator


def testImageLoaderReadsImageFromPath(tmp_path: Path) -> None:
    imagePath = tmp_path / "input.png"
    sampleImage = np.zeros((32, 32, 3), dtype=np.uint8)
    sampleImage[8:24, 8:24] = (255, 255, 255)
    cv2.imwrite(str(imagePath), sampleImage)

    operatorInstance = ImageLoaderOperator()
    result = operatorInstance.executeNode(
        inputs={},
        params={"imagePath": str(imagePath), "colorMode": "color"},
        runtimeContext={},
    )

    assert result["status"] == "ok"
    outputs = result.get("outputs", {})
    assert isinstance(outputs, dict)
    loadedImage = outputs.get("image")
    assert isinstance(loadedImage, np.ndarray)
    assert loadedImage.shape == sampleImage.shape


def testImageLoaderReturnsErrorWhenPathMissing() -> None:
    operatorInstance = ImageLoaderOperator()
    result = operatorInstance.executeNode(
        inputs={}, params={"imagePath": "", "colorMode": "color"}, runtimeContext={}
    )
    assert result["status"] == "error"
