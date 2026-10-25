"""DINO numerical recipe from Vision Workshop's dinov2_onnx_numeric.

Keep the FP32 half-pixel operation order: OpenCV/YOLO resize is not equivalent.
"""
from __future__ import annotations

import numpy as np


PREPROCESS = {
    "input_size": [256, 256], "color_space": "RGB", "input_range": [0.0, 1.0],
    "resize_mode": "letterbox", "interpolation": "bilinear", "align_corners": False,
    "antialias": False, "rounding": "python_round", "padding": "center_remainder_bottom_right",
    "fill_before_normalize": 0.0, "mean": [0.485, 0.456, 0.406], "std": [0.229, 0.224, 0.225],
    "patch_edge_policy": "floor_valid_no_extra_pad", "patch_coverage": [252, 252],
}


def resize(value, size):
    value = np.asarray(value, dtype=np.float32)
    h, w = value.shape[-2:]
    rh, rw = size
    if (h, w) == (rh, rw):
        return value.copy()

    def coordinates(old, new):
        scale = np.float32(old / new)
        position = np.maximum(np.float32(0), scale * (np.arange(new, dtype=np.float32) + np.float32(.5)) - np.float32(.5))
        lower = position.astype(np.int64)
        return lower, np.minimum(lower + 1, old - 1), position - lower.astype(np.float32)

    y0, y1, wy = coordinates(h, rh)
    x0, x1, wx = coordinates(w, rw)
    # Horizontal pairs first, then the vertical pair, matching the producer.
    a = value[..., y0[:, None], x0] * (1-wx) + value[..., y0[:, None], x1] * wx
    b = value[..., y1[:, None], x0] * (1-wx) + value[..., y1[:, None], x1] * wx
    return a * (1-wy[:, None]) + b * wy[:, None]


def preprocess(rgb):
    if (not isinstance(rgb, np.ndarray) or rgb.dtype != np.uint8 or rgb.ndim != 3
            or rgb.shape[2] != 3 or min(rgb.shape[:2]) < 1 or rgb.shape[0]*rgb.shape[1] > 40_000_000):
        raise ValueError("expected RGB HWC uint8, at most 40 million pixels")
    h, w = rgb.shape[:2]
    ratio = min(256/h, 256/w)
    rh, rw = max(1, round(h*ratio)), max(1, round(w*ratio))
    top, left = (256-rh)//2, (256-rw)//2
    value = rgb.transpose(2, 0, 1).astype(np.float32) / np.float32(255)
    resized = resize(value, (rh, rw))
    canvas = np.zeros((3, 256, 256), np.float32)
    canvas[:, top:top+rh, left:left+rw] = resized
    canvas = (canvas - np.array([.485, .456, .406], np.float32)[:, None, None]) / np.array([.229, .224, .225], np.float32)[:, None, None]
    return np.ascontiguousarray(canvas[None]), {
        "original_size": [h, w], "resized_size": [rh, rw],
        "padding": [left, 256-rw-left, top, 256-rh-top],
    }


def restore(rawMap, geometry):
    if rawMap.shape != (1, 1, 256, 256) or not np.isfinite(rawMap).all():
        raise ValueError("invalid raw anomaly map")
    left, _, top, _ = geometry["padding"]
    rh, rw = geometry["resized_size"]
    return resize(rawMap[0, 0, top:top+rh, left:left+rw], geometry["original_size"])


def decision(score, threshold):
    if not np.isfinite(score) or type(threshold) not in (int, float) or not np.isfinite(threshold):
        raise ValueError("nonfinite score/threshold")
    return float(score) >= float(threshold)
