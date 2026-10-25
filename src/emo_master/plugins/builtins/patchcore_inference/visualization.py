"""Display-only localization; relative responses are not calibrated pixel labels."""
from __future__ import annotations

import cv2
import numpy as np


def highlightOverlay(bgr, heat, relativeThreshold):
    overlay = bgr.copy()
    low, high = float(heat.min()), float(heat.max())
    span = high - low
    # Restoring a uniform FP32 map can introduce a few rounding ULPs.
    tolerance = max(1e-12, 4 * np.finfo(np.float32).eps * max(abs(low), abs(high)))
    if span <= tolerance:
        return overlay, False
    relative = (heat.astype(np.float64) - low) / span
    selected = relative >= relativeThreshold
    pixels = bgr[selected]
    overlay[selected] = cv2.addWeighted(pixels, .6, np.full_like(pixels, (0, 0, 255)), .4, 0)
    contours, _ = cv2.findContours(selected.astype(np.uint8), cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    cv2.drawContours(overlay, contours, -1, (0, 0, 0), 4)
    cv2.drawContours(overlay, contours, -1, (0, 255, 255), 2)
    return overlay, True
