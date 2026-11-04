"""Compatibility entry point for the shared pure desktop log exporter."""
from emo_master.core.log_export import LogExportCancelled, exportLogs, os as os

__all__ = ["LogExportCancelled", "exportLogs"]
