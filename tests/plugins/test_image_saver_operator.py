from pathlib import Path

import numpy as np

from emo_master.plugins.builtins.image_saver.operator import ImageSaverOperator


def testImageSaverWritesImageToPath(tmp_path: Path) -> None:
    outputPath = tmp_path / "saved.png"
    image = np.zeros((24, 24, 3), dtype=np.uint8)
    image[:, :] = (10, 20, 30)

    operatorInstance = ImageSaverOperator()
    result = operatorInstance.executeNode(
        inputs={"image": image},
        params={"outputPath": str(outputPath), "overwrite": True},
        runtimeContext={},
    )

    assert result["status"] == "ok"
    assert outputPath.exists()


def testImageSaverRejectsOverwriteWhenDisabled(tmp_path: Path) -> None:
    outputPath = tmp_path / "locked.png"
    outputPath.write_bytes(b"existing")
    image = np.zeros((16, 16, 3), dtype=np.uint8)

    operatorInstance = ImageSaverOperator()
    result = operatorInstance.executeNode(
        inputs={"image": image},
        params={"outputPath": str(outputPath), "overwrite": False},
        runtimeContext={},
    )

    assert result["status"] == "error"
