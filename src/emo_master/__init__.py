__version__ = "0.6.1"


def _preloadOnnxRuntimeBeforeQt() -> None:
    """Load ONNX Runtime before PySide2 installs its native import hooks."""
    try:
        __import__("onnxruntime")
    except ImportError:
        # Keep the package importable so the YOLO operator can report its
        # established E_BACKEND_UNAVAILABLE error when the dependency is absent.
        return


_preloadOnnxRuntimeBeforeQt()
