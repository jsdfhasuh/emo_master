"""Normalize local debug images to the existing PNG asset protocol."""
from io import BytesIO
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from emo_master.apps.runtime.operator_debug.assets import VALUE_BYTES, decode, imageBytes


def inputFileFilter(portType):
    return "Images (*.png *.jpg *.jpeg)" if portType == "image" else "PNG / JSON (*.png *.json)"


def readInputFile(path, portType):
    file = Path(path)
    with file.open("rb") as stream:
        raw = stream.read(VALUE_BYTES + 1)
    if len(raw) > VALUE_BYTES:
        raise ValueError("Input file exceeds 64 MiB")
    mime = "image/png" if file.suffix.lower() == ".png" else "application/json"
    if portType == "image":
        # Check dimensions before allocating decoded pixels, including alpha.
        try:
            header = Image.open(BytesIO(raw), formats=("PNG", "JPEG"))
        except OSError as error:
            raise ValueError("Cannot decode image input; select a PNG or JPEG image") from error
        with header:
            channels = {"1": 1, "L": 1, "RGB": 3, "YCbCr": 3}.get(header.mode, 4)
            if header.width * header.height * channels > VALUE_BYTES:
                raise ValueError("Decoded image exceeds 64 MiB")
        pixels = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_UNCHANGED)
        if pixels is None:
            raise ValueError("Cannot decode image input")
        if pixels.dtype != np.uint8:
            raise ValueError("Image input must use 8-bit pixels")
        if pixels.ndim == 3 and pixels.shape[2] == 4:
            pixels = cv2.cvtColor(pixels, cv2.COLOR_BGRA2BGR)
        raw, mime = imageBytes(pixels), "image/png"
    decode(raw, mime)
    return raw, mime, dict(kind="upload", filename=file.name)
