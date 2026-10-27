from .app_version import APP_VERSION as __version__
from .qt_environment import prepareQtEnvironment as _prepareQtEnvironment

__all__ = ["__version__"]


def _preloadOnnxRuntimeBeforeQt() -> None:
    """Load ONNX Runtime before PySide2 installs its native import hooks."""
    try:
        __import__("onnxruntime")
    except ImportError:
        # Keep the package importable so the YOLO operator can report its
        # established E_BACKEND_UNAVAILABLE error when the dependency is absent.
        return


_preloadOnnxRuntimeBeforeQt()

_prepareQtEnvironment()
