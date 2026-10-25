"""Frozen outputs from Vision Workshop dinov2_onnx_numeric (NumPy 1.26.4).

Reference SHA256: 8deb7e7ef96d4f0e73440a36c4feb6ad2c99210a017165d7a4e6f54d0c08ad92.
No training-platform import or path is required to run these tests.
"""
import hashlib

import numpy as np
import pytest

from emo_master.plugins.builtins.patchcore_inference import numeric


@pytest.mark.parametrize("shape,resized,padding,tensorHash,mapHash", [
    ((140, 360), [100, 256], [0, 0, 78, 78],
     "5209461f16d9035d707fcedf681da895ed6adfc80ff9be3722e750c1b25d8b27",
     "d3645010233e8228c4b7b5e23c1f30344d08b8bdd03ca8af296e701966592bed"),
    ((360, 140), [256, 100], [78, 78, 0, 0],
     "f37a1fa6b5fbfc3d74de4403d64509d39845afe26855feebc07c659a2d859e7d",
     "8aaf339e0d4f9ae79135770e597549052b3672e2a9db8b82f2e44df9801d9d37"),
    ((113, 251), [115, 256], [0, 0, 70, 71],
     "dfc7e99951c6d731b56d640e60fc92f328cf57894c601f4684981eb3ff22b88b",
     "9f821310ff31ffc6a2d3c4261332619b47aa47bedbd7238bf1704c63991d949f"),
    ((1, 20), [13, 256], [0, 0, 121, 122],
     "608f87fc8131fd7adf8fb92b0e1b6c08cf3e58edb49071a5f3e695167fe4e0c2",
     "55cc9a09b85eccb4a25dd21c1d17484f69622eb25bf2b6cd1b7e5c5df45501d7"),
    ((19, 1), [256, 13], [121, 122, 0, 0],
     "5dacd016e3c609959f5d6413ba3249d76b0935df485b70d82a00739d854efb47",
     "343b5cbbc5e3330d5dea82d202162bc2216c1f4aca6db18453bcc0608e7a670f"),
    ((511, 717), [182, 256], [0, 0, 37, 37],
     "638bf111365ebfe153bdff7406abb4b43116468fee3400cec1588f78b5887bb0",
     "f20b5eecde7948f22dafb3d8eca1956515e9e3d86b8025ce6f1b3ea94cf82713"),
    ((256, 256), [256, 256], [0, 0, 0, 0],
     "fe959d0b81c557214c9d5037a6f8901e2b1d95119283428ab9be9ad368728fd9",
     "c3ef8614f13b75398481f6645f7fd58c6e9ea27e014f2372c98e04416f2aa2c3"),
])
def testExactProducerPreprocessAndRestore(shape, resized, padding, tensorHash, mapHash):
    rgb = np.random.default_rng(7).integers(0, 256, (*shape, 3), dtype=np.uint8)
    original = rgb.copy()
    raw = np.random.default_rng(3).random((1, 1, 256, 256), dtype=np.float32)
    originalRaw = raw.copy()
    tensor, geometry = numeric.preprocess(rgb)
    restored = numeric.restore(raw, geometry)
    assert geometry == {"original_size": list(shape), "resized_size": resized, "padding": padding}
    assert tensor.dtype == restored.dtype == np.float32
    assert hashlib.sha256(tensor.tobytes()).hexdigest() == tensorHash
    assert hashlib.sha256(restored.tobytes()).hexdigest() == mapHash
    np.testing.assert_array_equal(rgb, original)
    np.testing.assert_array_equal(raw, originalRaw)
