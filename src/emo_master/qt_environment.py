"""Keep OpenCV's private Qt build out of the PySide2 application environment."""
from __future__ import annotations

import os
from pathlib import Path
import sys


def prepareQtEnvironment() -> None:
    if not sys.platform.startswith("linux"):
        return
    try:
        # The non-headless cv2 wheel installs these variables at import time.
        # Import it once before Qt so a later operator import cannot undo this.
        import cv2
    except ImportError:
        return
    private = Path(cv2.__file__).resolve().parent / "qt"
    for key in ("QT_QPA_PLATFORM_PLUGIN_PATH", "QT_QPA_FONTDIR"):
        value = os.environ.get(key)
        if value and Path(value).resolve().is_relative_to(private):
            # Do not overwrite a caller-specified unrelated Qt installation.
            os.environ.pop(key, None)
