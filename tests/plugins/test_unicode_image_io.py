import cv2
import numpy as np
import pytest

from emo_master.plugins.builtins.image_loader.operator import ImageLoaderOperator
from emo_master.plugins.builtins.image_saver.operator import ImageSaverOperator


@pytest.mark.parametrize('mode', ['color', 'grayscale'])
def testUnicodeImageRoundtripUsesSamePixelsAndFrame(tmp_path, mode):
    root = tmp_path/'中文 空格'
    root.mkdir()
    path = root/'输入 结果.png'
    pixels = np.random.default_rng(19).integers(0, 256, (24, 32, 3), dtype=np.uint8)
    saver, loader = ImageSaverOperator(), ImageLoaderOperator()
    saved = saver.executeNode({'image': pixels}, {'outputPath': str(path)}, {})
    assert saved['status'] == 'ok'
    loaded = loader.executeNode({}, {'imagePath': str(path), 'colorMode': mode}, {})
    assert loaded['status'] == 'ok'
    expected = pixels if mode == 'color' else cv2.imdecode(np.frombuffer(path.read_bytes(), np.uint8), cv2.IMREAD_GRAYSCALE)
    np.testing.assert_array_equal(loaded['outputs']['image'], expected)
    before = path.read_bytes()
    rejected = saver.executeNode({'image': pixels}, {'outputPath': str(path), 'overwrite': False}, {})
    assert rejected['error']['code'] == 'E_OUTPUT_EXISTS'
    assert path.read_bytes() == before
    path.write_bytes(b'not an image')
    assert loader.executeNode({}, {'imagePath': str(path)}, {})['error']['code'] == 'E_INPUT_DECODE'
